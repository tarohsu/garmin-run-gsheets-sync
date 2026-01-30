import os
import json
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
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 Running Advanced Multi-Key Data Extraction...")
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
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        dist_m, dur_s = activity.get('distance', 0), activity.get('duration', 0)
        
        # --- 暴力提取：嘗試所有可能的 Garmin 欄位名稱 ---
        
        # 1. 步幅 (Step Length): 嘗試不同的單位換算
        # 網頁顯示 0.98m，API 可能給 98.0 (cm) 或 980.0 (mm) 或 0.98 (m)
        s_len_raw = (activity.get('avgStepLength') or activity.get('averageStepLength') or 
                     activity.get('avgStepLengthMeters') or 0)
        if s_len_raw > 200: # 可能是公釐 mm (如 980)
            step_m = round(s_len_raw / 1000, 2)
        elif s_len_raw > 10: # 可能是公分 cm (如 98)
            step_m = round(s_len_raw / 100, 2)
        else: # 可能是公尺 m (如 0.98)
            step_m = round(s_len_raw, 2)
            
        # 2. 功率 (Running Power)
        pwr = (activity.get('avgRunningPower') or activity.get('averageRunningPower') or 
               activity.get('avgPower') or activity.get('averagePower') or 0)
               
        # 3. 溫度 (Temperature)
        temp = (activity.get('avgTemperature') or activity.get('averageTemperature') or 
                activity.get('minTemperature') or 0)

        row = [
            date, activity.get('activityName', 'Run'), round(dist_m/1000, 2), 
            format_to_time_string(dur_s), format_pace_string(dist_m, dur_s),
            activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0,
            activity.get('calories', 0) or 0, round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0),
            int(activity.get('elevationGain', 0) or 0), activity.get('activityType', {}).get('typeKey', 'run'),
            round(activity.get('aerobicTrainingEffect', 0), 1),
            round(activity.get('anaerobicTrainingEffect', 0), 1),
            step_m, pwr, temp
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✨ 成功批量置頂 {len(rows_to_insert)} 筆活動 (步幅/功率已強化提取)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
