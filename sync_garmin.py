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
    if distance_meters <= 0: return "0:00"
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 啟動 3 個月主表同步 (無分段/無無效欄位)...")
    
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

    # 3. 設定時間範圍：近 3 個月 (90天)
    # 先抓清單篩選
    summary_list = garmin.get_activities(0, 100)
    
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")

    # 4. 連結 Google Sheets
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    
    # 取得現有資料日期，避免重複
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

    print(f"📦 發現 {len(target_activities)} 筆新資料...")

    rows_main = []
    
    # 5. 逐筆抓取詳情 (使用 get_activity 確保數據完整)
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

            # === 核心修正：正確抓取 summaryDTO ===
            summary = full_data.get('summaryDTO') or full_data
            
            dist_m = summary.get('distance', 0)
            dur_s = summary.get('duration', 0)

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

            # 觸地平衡 (若無則顯示 --)
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
            
            # 其他基礎數據
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

            # 整理欄位 (共 24 欄，已移除流汗/呼吸率)
            row_main = [
                date,                               # A: 日期
                name,                               # B: 活動名稱
                round(dist_m / 1000, 2),            # C: 距離
                format_to_time_string(dur_s),       # D: 時間
                format_pace_string(dist_m, dur_s),  # E: 配速
                vo2,                                # F: VO2 Max
                hr,                                 # G: 平均心率
                max_hr,                             # H: 最大心率
                aerobic,                            # I: 有氧 TE
                anaerobic,                          # J: 無氧 TE
                cadence,                            # K: 平均步頻
                step_m,                             # L: 平均步幅
                move_eff,                           # M: 移動效率
                vo_cm,                              # N: 垂直振幅
                gct,                                # O: 觸地時間
                gct_bal_str,                        # P: 觸地平衡
                pwr_norm,                           # Q: NP
                pwr_avg,                            # R: 平均功率
                pwr_max,                            # S: 最大功率
                elev_gain,                          # T: 總爬升
                min_elev,                           # U: 最低海拔
                max_elev,                           # V: 最高海拔
                steps,                              # W: 總步數
                cal                                 # X: 卡路里
            ]
            rows_main.append(row_main)
            
            time.sleep(1) # 避免 API 限制

        except Exception as e:
            print(f"❌ 處理 {date} 失敗: {e}")

    # 6. 寫入主表
    if rows_main:
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 主表同步完成: 新增 {len(rows_main)} 筆 (近3個月)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
