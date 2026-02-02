import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

def format_to_time_string(seconds):
    if not seconds: return "0:00"
    seconds = int(round(seconds))
    m, s = seconds // 60, seconds % 60
    return f"{m}:{s:02d}"

def format_pace_string(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return "0:00"
    # 避免除以零
    if distance_meters <= 0: return "0:00"
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 啟動 FR965 結構修正同步 (針對 summaryDTO 與 splitSummaries)...")
    
    # 1. 讀取環境變數
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not all([garmin_email, garmin_password, google_creds_json, sheet_id]):
        print("❌ 錯誤：Secrets 設定不完整")
        return

    # 2. 登入 Garmin
    try:
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        print("✅ Garmin 登入成功")
    except Exception as e:
        print(f"❌ Garmin 登入失敗: {e}")
        return

    # 3. 連結 Google Sheets
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    try:
        sheet_splits = client.open("Garmin Data").worksheet("Garmin Splits")
    except:
        print("❌ 找不到 'Garmin Splits' 分頁")
        return
    
    # 4. 鎖定範圍 (先抓清單篩選日期)
    # 擴大搜尋範圍以確保包含近3個月
    summary_list = garmin.get_activities(0, 100)
    
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")

    existing_dates = {row[0] for row in sheet_main.get_all_values()[1:] if row}
    
    target_activities = []
    for a in summary_list:
        start_time = a.get('startTimeLocal', '')
        if not start_time: continue
        
        act_date = datetime.strptime(start_time[:10], '%Y-%m-%d')
        if act_date < cutoff_date: continue
        
        # 只抓跑步
        if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']:
            if start_time[:10] not in existing_dates:
                target_activities.append(a)

    print(f"📦 發現 {len(target_activities)} 筆新資料 (將進入深層結構提取)...")

    rows_main = []
    rows_splits = []
    
    # 5. 逐筆抓取詳情
    for index, item in enumerate(target_activities):
        activity_id = item.get('activityId')
        date = item.get('startTimeLocal', '')[:10]
        name = item.get('activityName', 'Run')
        
        print(f"🔄 [{index+1}/{len(target_activities)}] 下載與解析: {date} - {name}...")
        
        try:
            # 獲取完整數據
            full_data = garmin.get_activity(activity_id)
            if isinstance(full_data, str):
                full_data = json.loads(full_data)

            # === 關鍵修正：進入 summaryDTO 抓取數據 ===
            # 如果 summaryDTO 不存在，退而求其次用 full_data (有些舊資料可能結構不同)
            summary = full_data.get('summaryDTO') or full_data
            
            dist_m = summary.get('distance', 0)
            dur_s = summary.get('duration', 0)
            
            # 如果距離為0，可能是資料異常，但我們還是照實記錄
            if dist_m == 0:
                print(f"⚠️ 注意: {date} 距離為 0")

            # 進階指標
            stride = summary.get('avgStrideLength') or 0
            step_m = round(stride / 100, 2) if stride > 10 else round(stride, 2)
            
            gct = int(round(summary.get('avgGroundContactTime') or 0))
            vo = summary.get('avgVerticalOscillation') or 0
            vo_cm = round(vo / 10, 1) if vo > 20 else round(vo, 1)
            
            vr = summary.get('avgVerticalRatio') or 0
            if vr == 0 and stride > 0 and vo > 0:
                vr = (vo / (stride * 10)) * 100
            move_eff = round(vr, 1)

            # 觸地平衡 (沒有就顯示 --)
            gct_bal = summary.get('avgGroundContactBalance')
            if gct_bal:
                if gct_bal > 100: gct_bal /= 100
                gct_bal_str = f"{gct_bal}% L / {round(100 - gct_bal, 1)}% R"
            else:
                gct_bal_str = "--"

            # 功率
            pwr_avg = int(summary.get('avgPower', 0) or summary.get('avgRunningPower', 0) or 0)
            pwr_max = int(summary.get('maxPower', 0) or 0)
            pwr_norm = int(summary.get('normPower', 0) or 0)
            
            # 流汗與呼吸 (若無則 0)
            sweat = int(summary.get('totalSweatLoss') or summary.get('sweatLoss') or 0)
            resp_rate = int(summary.get('avgRespirationRate') or 0)
            
            # 其他
            steps = summary.get('steps', 0)
            min_elev = int(summary.get('minElevation') or 0)
            max_elev = int(summary.get('maxElevation') or 0)
            vo2 = int(summary.get('vO2MaxValue') or 0)
            cal = int(summary.get('calories') or 0)
            
            hr = summary.get('averageHR') or 0
            max_hr = summary.get('maxHR') or 0
            cadence = round(summary.get('averageRunningCadenceInStepsPerMinute', 0), 0)
            aerobic = round(summary.get('aerobicTrainingEffect', 0), 1)
            anaerobic = round(summary.get('anaerobicTrainingEffect', 0), 1)
            elev_gain = int(summary.get('elevationGain', 0) or 0)

            # 26 欄
            row_main = [
                date, name, round(dist_m / 1000, 2), format_to_time_string(dur_s), format_pace_string(dist_m, dur_s),
                vo2, hr, max_hr, resp_rate, aerobic, anaerobic,
                cadence, step_m, move_eff, vo_cm, gct, gct_bal_str,
                pwr_norm, pwr_avg, pwr_max,
                elev_gain, min_elev, max_elev, steps, sweat, cal
            ]
            rows_main.append(row_main)

            # === 關鍵修正：進入 splitSummaries 抓分段 ===
            # Log 顯示這裡有東西
            splits_list = full_data.get('splitSummaries', [])
            
            if splits_list:
                for i, split in enumerate(splits_list):
                    # 有時候 splits 裡的 key 也可能不一樣，我們小心處理
                    s_dist = split.get('distance', 0)
                    s_dur = split.get('duration', 0)
                    
                    # 避免 0 距離的分段 (例如休息段有時會很怪)
                    # if s_dist == 0: continue 
                    
                    s_stride = split.get('avgStrideLength') or 0
                    s_step_m = round(s_stride / 100, 2) if s_stride > 10 else round(s_stride, 2)
                    
                    s_pwr = int(split.get('avgPower') or split.get('avgRunningPower') or 0)
                    s_elev = int(split.get('elevationGain') or 0)
                    
                    row_split = [
                        date, name, i + 1, round(s_dist / 1000, 2),
                        format_to_time_string(s_dur), format_pace_string(s_dist, s_dur),
                        split.get('averageHR', 0) or 0, split.get('maxHR', 0) or 0,
                        round(split.get('averageRunningCadenceInStepsPerMinute', 0), 0),
                        s_step_m, s_pwr, s_elev
                    ]
                    rows_splits.append(row_split)
            else:
                # 這次不應該再發生了，除非真的沒分段
                print(f"⚠️ {date} 依然無分段 (Key 'splitSummaries' is empty)")

            time.sleep(1) # 禮貌性延遲

        except Exception as e:
            print(f"❌ 處理 {date} 失敗: {e}")

    # 6. 寫入
    if rows_main:
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 主表同步: {len(rows_main)} 筆 (數值修正完成)")
    
    if rows_splits:
        sheet_splits.insert_rows(rows_splits, 2)
        print(f"✅ 分段同步: {len(rows_splits)} 圈 (抓取 splitSummaries)")

    if not rows_main:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
