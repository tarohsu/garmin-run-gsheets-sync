import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

# 新增：精準轉換秒數為 MM:SS 或 HH:MM:SS
def format_to_time_string(seconds):
    if not seconds: return "0:00"
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

# 新增：精準計算配速並轉為 MM:SS
def format_pace_string(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return "0:00"
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 Starting Definitive Fix Sync...")
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
    
    for activity in reversed(running_activities):
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # --- 進階指標暴力偵測 (解決 0 的問題) ---
        # 步幅 (mm/cm 換算)
        s_len = activity.get('avgStepLength') or activity.get('averageStepLength') or 0
        s_len = round(s_len / 100, 2) if s_len > 10 else round(s_len, 2)
        
        # 功率與溫度 (嘗試多重 Key)
        pwr = activity.get('avgRunningPower') or activity.get('averageRunningPower') or activity.get('avgPower') or 0
        temp = activity.get('avgTemperature') or activity.get('averageTemperature') or 0

        row = [
            date, 
            activity.get('activityName', 'Run'), 
            round(dist_m/1000, 2), 
            format_to_time_string(dur_s),    # 修正：轉為 55:16 格式
            format_pace_string(dist_m, dur_s), # 修正：轉為 5:31 格式
            activity.get('averageHR', 0) or 0, 
            activity.get('maxHR', 0) or 0,
            activity.get('calories', 0) or 0, 
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0),
            int(activity.get('elevationGain', 0) or 0), # 修正：整數呈現
            activity.get('activityType', {}).get('typeKey', 'run'),
            round(activity.get('aerobicTrainingEffect', 0), 1),
            round(activity.get('anaerobicTrainingEffect', 0), 1),
            s_len, pwr, temp
        ]
        sheet.insert_row(row, 2)
        print(f"✅ 同步成功: {date}")

if __name__ == "__main__": main()
