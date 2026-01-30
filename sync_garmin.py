import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

def format_duration(seconds):
    return round(seconds / 60, 2) if seconds else 0

def format_pace(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return 0
    pace_seconds = duration_seconds / (distance_meters / 1000)
    return round(pace_seconds / 60, 2)

def main():
    print("🚀 Starting Professional Garmin Sync...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('GOOGLE_CREDENTIALS'), os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    activities = garmin.get_activities(0, 50)
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    sheet = client.open("Garmin Data").worksheet("Garmin Data")
    
    existing_dates = {row[0] for row in sheet.get_all_values()[1:] if row}
    running_activities = [a for a in activities if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']]
    
    for activity in reversed(running_activities):
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        # --- 精準提取進階數據 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # 1. 訓練效果 (修正 4.19999 問題)
        a_te = round(activity.get('aerobicTrainingEffect', 0), 1)
        an_te = round(activity.get('anaerobicTrainingEffect', 0), 1)
        
        # 2. 步幅 (處理 mm, cm, m 單位差異)
        # 優先找網頁看到的 0.98m 欄位
        raw_step = activity.get('avgStepLength') or activity.get('averageStepLength') or 0
        step_len = round(raw_step / 100, 2) if raw_step > 10 else round(raw_step, 2)

        # 3. 功率 (嘗試不同 Key 路徑)
        pwr = activity.get('avgRunningPower') or activity.get('averageRunningPower') or 0
        
        # 4. 溫度
        temp = activity.get('avgTemperature') or activity.get('averageTemperature') or 0

        row = [
            date, activity.get('activityName', 'Run'), round(dist_m/1000, 2), format_duration(dur_s), 
            format_pace(dist_m, dur_s), activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0,
            activity.get('calories', 0) or 0, round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0),
            round(activity.get('elevationGain', 0), 1), activity.get('activityType', {}).get('typeKey', 'run'),
            a_te, an_te, step_len, pwr, temp
        ]
        
        sheet.insert_row(row, 2) # 最新置頂
        print(f"✅ 同步成功: {date} (功率: {pwr}W, 步幅: {step_len}m)")

    print("✨ Sync Complete!")

if __name__ == "__main__": main()
