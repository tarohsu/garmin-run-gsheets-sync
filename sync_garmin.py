import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

def format_duration(seconds):
    return round(seconds / 60, 2) if seconds else 0

def format_pace(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds:
        return 0
    distance_km = distance_meters / 1000
    pace_seconds = duration_seconds / distance_km
    return round(pace_seconds / 60, 2)

def main():
    print("🚀 Starting Professional Garmin Sync (Newest First)...")
    
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not all([garmin_email, garmin_password, google_creds_json, sheet_id]):
        print("❌ 錯誤：缺少 Secrets 設定")
        return
    
    # 連結 Garmin
    try:
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        activities = garmin.get_activities(0, 100)
        running_activities = [
            a for a in activities 
            if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']
        ]
        print(f"✅ 成功抓取 {len(running_activities)} 筆活動")
    except Exception as e:
        print(f"❌ Garmin 錯誤: {e}")
        return
    
    # 連結 Google Sheets
    try:
        creds = Credentials.from_service_account_info(
            json.loads(google_creds_json),
            scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        )
        client = gspread.authorize(creds)
        sheet = client.open("Garmin Data").worksheet("Garmin Data")
    except Exception as e:
        print(f"❌ Sheets 錯誤: {e}")
        return
    
    # 取得現有日期
    existing_rows = sheet.get_all_values()
    existing_dates = {row[0] for row in existing_rows[1:] if row}
    
    # 處理資料 (注意：不使用 reversed，因為 API 回傳本身就是由新到舊)
    new_entries = 0
    for activity in running_activities:
        activity_date = activity.get('startTimeLocal', '')[:10]
        
        if activity_date in existing_dates:
            continue
            
        try:
            dist_m = activity.get('distance', 0)
            dur_s = activity.get('duration', 0)
            
            # 格式化數據
            aerobic_te = round(activity.get('aerobicTrainingEffect', 0), 1)
            anaerobic_te = round(activity.get('anaerobicTrainingEffect', 0), 1)
            
            raw_step = activity.get('avgStepLength') or activity.get('averageStepLength') or 0
            step_len = round(raw_step / 100, 2) if raw_step > 10 else round(raw_step, 2)
                
            row_data = [
                activity_date,
                activity.get('activityName', 'Run'),
                round(dist_m / 1000, 2),
                format_duration(dur_s),
                format_pace(dist_m, dur_s),
                activity.get('averageHR', 0) or 0,
                activity.get('maxHR', 0) or 0,
                activity.get('calories', 0) or 0,
                activity.get('averageRunningCadenceInStepsPerMinute', 0) or 0,
                round(activity.get('elevationGain', 0), 1),
                activity.get('activityType', {}).get('typeKey', 'running'),
                aerobic_te,
                anaerobic_te,
                step_len,
                activity.get('avgRunningPower') or 0,
                activity.get('avgTemperature') or 0
            ]
            
            # 關鍵修改：將資料插入到第 2 列 (標題列下方)
            sheet.insert_row(row_data, 2)
            print(f"新增最新資料: {activity_date}")
            new_entries += 1
            
        except Exception as e:
            print(f"⚠️ 略過 {activity_date}: {e}")
            continue

    print(f"\n✨ 同步完成！{new_entries} 筆新紀錄已置頂。")

if __name__ == "__main__":
    main()
