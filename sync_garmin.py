import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread
from datetime import datetime

# 格式化時間：秒轉分鐘
def format_duration(seconds):
    return round(seconds / 60, 2) if seconds else 0

# 格式化配速：秒/公尺轉分鐘/公里
def format_pace(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds:
        return 0
    distance_km = distance_meters / 1000
    pace_seconds = duration_seconds / distance_km
    return round(pace_seconds / 60, 2)

def main():
    print("🚀 Starting Professional Garmin Sync...")
    
    # 1. 取得環境變數 (GitHub Secrets)
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not all([garmin_email, garmin_password, google_creds_json, sheet_id]):
        print("❌ 錯誤：缺少必要的環境變數 (Secrets)")
        return
    
    # 2. 登入 Garmin
    print("🔗 Connecting to Garmin...")
    try:
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        print("✅ Garmin 登入成功")
    except Exception as e:
        print(f"❌ Garmin 登入失敗: {e}")
        return
    
    # 3. 抓取活動 (設定 100 筆，確保歷史資料完整)
    try:
        activities = garmin.get_activities(0, 100)
        running_activities = [
            a for a in activities 
            if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']
        ]
        print(f"📦 找到 {len(running_activities)} 筆跑步活動")
    except Exception as e:
        print(f"❌ 無法取得活動: {e}")
        return
    
    # 4. 連結 Google Sheets
    print("📊 Connecting to Google Sheets...")
    try:
        creds_dict = json.loads(google_creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        )
        client = gspread.authorize(creds)
        # 開啟試算表與指定分頁
        sheet = client.open("Garmin Data").worksheet("Garmin Data")
        print("✅ Google Sheets 連結成功")
    except Exception as e:
        print(f"❌ Google Sheets 連結失敗: {e}")
        return
    
    # 5. 取得現有日期避免重複
    existing_rows = sheet.get_all_values()
    existing_dates = {row[0] for row in existing_rows[1:] if row}
    
    # 6. 開始處理資料 (由舊到新排序)
    new_entries = 0
    for activity in reversed(running_activities):
        activity_date = activity.get('startTimeLocal', '')[:10]
        
        # 跳過已存在的日期
        if activity_date in existing_dates:
            continue
            
        try:
            # --- 核心數據 ---
            dist_m = activity.get('distance', 0)
            dur_s = activity.get('duration', 0)
            
            # --- 進階數據提取 (加入備用 Key 與單位換算) ---
            
            # 訓練效果：修正浮點數 4.19999...
            aerobic_te = round(activity.get('aerobicTrainingEffect', 0), 1)
            anaerobic_te = round(activity.get('anaerobicTrainingEffect', 0), 1)
            
            # 步幅：偵測單位 (Garmin 有時傳公釐 mm，有時傳公分 cm)
            raw_step = activity.get('avgStepLength') or activity.get('averageStepLength') or 0
            if raw_step > 500: # 可能是公釐 (如 1050mm = 1.05m)
                step_len = round(raw_step / 1000, 2)
            elif raw_step > 10: # 可能是公分 (如 105cm = 1.05m)
                step_len = round(raw_step / 100, 2)
            else:
                step_len = round(raw_step, 2)
                
            # 跑步功率：檢查多個可能路徑
            avg_pwr = activity.get('avgRunningPower') or activity.get('averageRunningPower') or 0
            
            # 溫度
            avg_temp = activity.get('avgTemperature') or activity.get('averageTemperature') or 0
            
            # 依照試算表欄位順序組成 Row
            row_data = [
                activity_date,                                      # A: 日期
                activity.get('activityName', 'Run'),                # B: 標題
                round(dist_m / 1000, 2),                            # C: 距離(km)
                format_duration(dur_s),                             # D: 時間(min)
                format_pace(dist_m, dur_s),                         # E: 平均配速
                activity.get('averageHR', 0) or 0,                  # F: 平均心率
                activity.get('maxHR', 0) or 0,                      # G: 最大心率
                activity.get('calories', 0) or 0,                   # H: 卡路里
                activity.get('averageRunningCadenceInStepsPerMinute', 0) or 0, # I: 步頻
                round(activity.get('elevationGain', 0), 1),         # J: 總爬升
                activity.get('activityType', {}).get('typeKey', 'running'), # K: 類型
                aerobic_te,                                         # L: Aerobic TE
                anaerobic_te,                                       # M: Anaerobic TE
                step_len,                                           # N: Step Length (m)
                avg_pwr,                                            # O: Avg Power (W)
                avg_temp                                            # P: Avg Temp (C)
            ]
            
            sheet.append_row(row_data)
            print(f"✅ Added: {activity_date} - {dist_m/1000:.2f}km (TE: {aerobic_te})")
            new_entries += 1
            
        except Exception as e:
            print(f"⚠️ 跳過 {activity_date} 資料處理錯誤: {e}")
            continue

    print(f"\n✨ 同步完成！新增了 {new_entries} 筆紀錄。")

if __name__ == "__main__":
    main()
