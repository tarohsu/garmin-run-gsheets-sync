import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

# 修正：將秒數轉為標準 MM:SS 或 HH:MM:SS
def format_to_time_string(seconds):
    if not seconds: return "0:00"
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

# 修正：配速計算並轉為 MM:SS
def format_pace_string(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return "0:00"
    # 秒/公里
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 Starting Batch Sync (Resolving 429 & Formatting)...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('GOOGLE_CREDENTIALS'), os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    # 抓取最近 100 筆活動
    activities = garmin.get_activities(0, 100)
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    sheet = client.open("Garmin Data").worksheet("Garmin Data")
    
    # 取得現有日期
    existing_rows = sheet.get_all_values()
    existing_dates = {row[0] for row in existing_rows[1:] if row}
    
    # 過濾跑步活動
    running_activities = [a for a in activities if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']]
    
    rows_to_insert = []
    
    # Garmin API 預設就是由新到舊
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates:
            continue
            
        try:
            dist_m = activity.get('distance', 0)
            dur_s = activity.get('duration', 0)
            
            # --- 深度提取進階指標 (解決 0 的問題) ---
            # 步幅：Garmin 可能存為 mm (如 1050), 需要偵測
            s_len_raw = activity.get('avgStepLength') or activity.get('averageStepLength') or 0
            step_m = round(s_len_raw / 100, 2) if s_len_raw > 20 else round(s_len_raw, 2)
            
            # 功率：嘗試多個可能路徑
            pwr = activity.get('avgRunningPower') or activity.get('averageRunningPower') or activity.get('avgPower') or 0
            
            # 溫度
            temp = activity.get('avgTemperature') or activity.get('averageTemperature') or 0
            
            row = [
                date,                                       # A: Date
                activity.get('activityName', 'Run'),        # B: Activity Name
                round(dist_m / 1000, 2),                    # C: Distance (km)
                format_to_time_string(dur_s),               # D: Duration (55:16)
                format_pace_string(dist_m, dur_s),          # E: Avg Pace (5:31)
                activity.get('averageHR', 0) or 0,          # F: Avg HR
                activity.get('maxHR', 0) or 0,              # G: Max HR
                activity.get('calories', 0) or 0,           # H: Calories
                round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # I: Avg Cadence
                int(activity.get('elevationGain', 0) or 0), # J: Elevation Gain (整數)
                activity.get('activityType', {}).get('typeKey', 'run'), # K: Activity Type
                round(activity.get('aerobicTrainingEffect', 0), 1),   # L: Aerobic TE (4.1)
                round(activity.get('anaerobicTrainingEffect', 0), 1), # M: Anaerobic TE
                step_m,                                     # N: Avg Step Length (m)
                pwr,                                        # O: Avg Power (W)
                temp                                        # P: Avg Temp (C)
            ]
            rows_to_insert.append(row)
            
        except Exception as e:
            print(f"⚠️ Error processing {date}: {e}")
            continue

    # --- 關鍵修正：批量寫入解決 429 錯誤 ---
    if rows_to_insert:
        # 這裡的順序：Garmin 回傳最新在最前，所以直接插入到第 2 列
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✨ 成功同步 {len(rows_to_insert)} 筆最新活動！")
    else:
        print("✓ 資料已是最新，無需更新")

if __name__ == "__main__":
    main()
