import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

# --- 輔助函式 ---
def format_to_time_string(seconds):
    if not seconds: return "0:00"
    seconds = int(round(seconds))
    m, s = seconds // 60, seconds % 60
    return f"{m}:{s:02d}"

def format_pace_string(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return "0:00"
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 啟動 3 個月數據同步 (含深度除錯模式)...")
    
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
    # 抓取數量設大一點 (200筆)，然後用日期過濾
    activities = garmin.get_activities(0, 200) 
    
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")

    # 4. 連結 Google Sheets
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    try:
        sheet_splits = client.open("Garmin Data").worksheet("Garmin Splits")
    except:
        print("❌ 找不到 'Garmin Splits' 分頁，請手動建立！")
        return
    
    existing_dates = {row[0] for row in sheet_main.get_all_values()[1:] if row}
    
    # 5. 過濾活動 (只留跑步)
    running_activities = []
    for a in activities:
        # 日期檢查
        start_time = a.get('startTimeLocal', '')
        if not start_time: continue
        
        act_date = datetime.strptime(start_time[:10], '%Y-%m-%d')
        if act_date < cutoff_date:
            continue # 超過3個月就跳過
            
        if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']:
            running_activities.append(a)

    print(f"📦 範圍內共有 {len(running_activities)} 筆跑步資料")

    rows_main = []
    rows_splits = []
    api_call_count = 0
    
    # 6. 開始處理
    # 注意：我們這次不檢查 existing_dates，因為你要維持近3個月，
    # 為了確保舊資料(如果有缺漏)能補上，或者你可以選擇清空表格重新跑一次最乾淨。
    # 這裡邏輯維持：若已存在就跳過，避免重複。
    
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: 
            continue
            
        activity_id = activity.get('activityId')
        activity_name = activity.get('activityName', 'Run')
        
        # --- [主表] 欄位提取 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # 步幅
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 跑姿
        gct = int(round(activity.get('avgGroundContactTime') or 0))
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 移動效率
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            vr = (vo_raw / (stride_raw * 10)) * 100
        move_eff = round(vr, 1)

        # 觸地平衡 (如果硬體不支援，這裡就會是空)
        gct_bal_raw = activity.get('avgGroundContactBalance') or activity.get('avgGroundContactTimeBalance')
        if gct_bal_raw:
            if gct_bal_raw > 100: gct_bal_raw /= 100
            gct_bal_str = f"{gct_bal_raw}% L / {round(100 - gct_bal_raw, 1)}% R"
        else:
            gct_bal_str = "--" # 顯示 -- 代表無數據

        # 功率
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # 流汗與呼吸 (部分錶款無此數據)
        sweat = int(activity.get('estimatedSweatLoss') or activity.get('sweatLoss') or activity.get('totalSweatLoss') or 0)
        resp_rate = int(activity.get('avgRespirationRate') or 0)

        # 其他
        steps = activity.get('steps', 0)
        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)
        vo2 = int(activity.get('vO2MaxValue') or 0)
        cal = int(activity.get('calories') or 0)

        # 26 欄
        row_main = [
            date, activity_name, round(dist_m / 1000, 2), format_to_time_string(dur_s), format_pace_string(dist_m, dur_s),
            vo2, activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0, resp_rate, round(activity.get('aerobicTrainingEffect', 0), 1), round(activity.get('anaerobicTrainingEffect', 0), 1),
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), step_m, move_eff, vo_cm, gct, gct_bal_str,
            pwr_norm, pwr_avg, pwr_max,
            int(activity.get('elevationGain', 0) or 0), min_elev, max_elev, steps, sweat, cal
        ]
        rows_main.append(row_main)

        # --- [分段] 除錯與提取 ---
        try:
            # 1. 嘗試用標準方法抓 laps
            splits_data = garmin.get_activity_splits(activity_id)
            
            # 2. 如果標準方法抓不到，嘗試抓 raw details
            if not splits_data:
                details = garmin.get_activity_details(activity_id)
                splits_data = details.get('laps') or details.get('splits')
                
                # [除錯重點] 如果還是空的，印出結構讓我們看
                if not splits_data:
                    print(f"⚠️ {date} 無分段數據。Raw Keys: {list(details.keys())}")
            
            api_call_count += 1
            
            if splits_data:
                lap_count = 1
                for split in splits_data:
                    s_dist = split.get('distance', 0)
                    s_dur = split.get('duration', 0)
                    
                    s_stride = split.get('avgStrideLength') or 0
                    s_step_m = round(s_stride / 100, 2) if s_stride > 10 else round(s_stride, 2)
                    
                    s_pwr = int(split.get('avgPower') or split.get('avgRunningPower') or 0)
                    s_elev = int(split.get('elevationGain') or 0)

                    row_split = [
                        date, activity_name, lap_count, round(s_dist / 1000, 2),
                        format_to_time_string(s_dur), format_pace_string(s_dist, s_dur),
                        split.get('averageHR', 0) or 0, split.get('maxHR', 0) or 0,
                        round(split.get('averageRunningCadenceInStepsPerMinute', 0), 0),
                        s_step_m, s_pwr, s_elev
                    ]
                    rows_splits.append(row_split)
                    lap_count += 1
            else:
                print(f"⚠️ 跳過 {date} 分段：完全無數據回傳")

            if api_call_count >= 10:
                time.sleep(2)
                api_call_count = 0
                
        except Exception as e:
            print(f"⚠️ 無法取得 {date} 的分段詳情: {e}")

    # 寫入
    if rows_main:
        # 因為由新到舊，插入時我們反轉一下列表，或者直接插在第2列會變成最新的在最上面
        # 這裡維持原有邏輯：最新的活動插在第2列
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 主表更新: {len(rows_main)} 筆 (近3個月)")
    
    if rows_splits:
        sheet_splits.insert_rows(rows_splits, 2)
        print(f"✅ 分段更新: {len(rows_splits)} 圈數據")

    if not rows_main:
        print("✓ 資料已是最新 (或無新資料)")

if __name__ == "__main__": main()
