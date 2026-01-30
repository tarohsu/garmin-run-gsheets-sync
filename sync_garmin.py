import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

def format_to_time_string(seconds):
    if not seconds: return "0:00"
    seconds = int(round(seconds))
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"

def format_pace_string(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return "0:00"
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 Starting Batch Sync with Deep Data Extraction...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('GOOGLE_CREDENTIALS'), os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    activities = garmin.get_activities(0, 100)
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    sheet = client.open("Garmin Data").worksheet("Garmin Data")
    
    existing_dates = {row[0] for row in sheet.get_all_values()[1:] if row}
    running_activities = [a for a in activities if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']]
    
    rows_to_insert = []
    
    # 按照時間由新到舊處理
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # --- 暴力偵測進階指標 (解決 0 的問題) ---
        # 步幅偵測：有些在總結，有些在詳細指標裡
        s_len_raw = activity.get('avgStepLength') or activity.get('averageStepLength') or 0
        step_m = round(s_len_raw / 100, 2) if s_len_raw > 20 else round(s_len_raw, 2)
        
        # 功率與溫度
        pwr = activity.get('avgRunningPower') or activity.get('averageRunningPower') or 0
        temp = activity.get('avgTemperature') or activity.get('averageTemperature') or 0
        
        # 爬升 (取整數)
        elev = int(activity.get('elevationGain', 0) or 0)

        row = [
            date, activity.get('activityName', 'Run'), round(dist_m/1000, 2), 
            format_to_time_string(dur_s), format_pace_string(dist_m, dur_s),
            activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0,
            activity.get('calories', 0) or 0, round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0),
            elev, activity.get('activityType', {}).get('typeKey', 'run'),
            round(activity.get('aerobicTrainingEffect', 0), 1),
            round(activity.get('anaerobicTrainingEffect', 0), 1),
            step_m, pwr, temp
        ]
        rows_to_insert.append(row)

    # --- 關鍵修正：一次性批量插入到第 2 列之後 ---
    if rows_to_insert:
        # 批量插入所有新資料，這只會消耗 1 次 API 配額
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✨ 成功批量同步 {len(rows_to_insert)} 筆最新活動！")
    else:
        print("✓ 沒有發現需要同步的新資料")

if __name__ == "__main__": main()
