import os
import json
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread
from datetime import datetime, timedelta

# Load environment variables
if os.path.exists('.env'):
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

def format_duration(seconds):
    return round(seconds / 60, 2) if seconds else 0

def format_pace(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds:
        return 0
    distance_km = distance_meters / 1000
    pace_seconds = duration_seconds / distance_km
    return round(pace_seconds / 60, 2)

def main():
    print("Starting Garmin running activities sync...")
    
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not all([garmin_email, garmin_password, google_creds_json, sheet_id]):
        print("❌ Missing required environment variables")
        return
    
    # Connect to Garmin
    print("Connecting to Garmin...")
    try:
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        print("✅ Connected to Garmin")
    except Exception as e:
        print(f"❌ Failed to connect to Garmin: {e}")
        return
    
    # Get recent activities (抓取最近 100 筆，確保數據更完整)
    print("Fetching activities...")
    try:
        activities = garmin.get_activities(0, 100) 
        print(f"Found {len(activities)} total activities")
    except Exception as e:
        print(f"❌ Failed to fetch activities: {e}")
        return
    
    running_activities = [
        activity for activity in activities 
        if activity.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']
    ]
    
    print(f"Found {len(running_activities)} running activities")
    
    # Connect to Google Sheets
    try:
        creds_dict = json.loads(google_creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        )
        client = gspread.authorize(creds)
        # 使用你剛改好的檔名
        sheet = client.open("Garmin Data").worksheet("Garmin Data")
        print("✅ Connected to Google Sheets")
    except Exception as e:
        print(f"❌ Failed to connect to Google Sheets: {e}")
        return
    
    try:
        existing_data = sheet.get_all_values()
        existing_dates = {row[0] for row in existing_data[1:] if row}
        print(f"Found {len(existing_dates)} existing entries")
    except Exception as e:
        existing_dates = set()

    new_entries = 0
    # 按照時間由舊到新排序，確保寫入順序正確
    for activity in reversed(running_activities):
        try:
            activity_date = activity.get('startTimeLocal', '')[:10]
            
            if activity_date in existing_dates:
                continue
            
            # 提取基礎指標
            distance_meters = activity.get('distance', 0)
            duration_seconds = activity.get('duration', 0)
            
            # --- 提取進階指標 ---
            aerobic_te = activity.get('aerobicTrainingEffect', 0)
            anaerobic_te = activity.get('anaerobicTrainingEffect', 0)
            # 步幅單位通常是公分，轉成公尺
            avg_step_length = round(activity.get('avgStepLength', 0) / 100, 2) if activity.get('avgStepLength') else 0
            # 跑步功率
            avg_power = activity.get('avgRunningPower', 0)
            # 平均溫度
            avg_temp = activity.get('avgTemperature', 0)
            
            row = [
                activity_date,
                activity.get('activityName', 'Run'),
                round(distance_meters / 1000, 2),
                format_duration(duration_seconds),
                format_pace(distance_meters, duration_seconds),
                activity.get('averageHR', 0) or 0,
                activity.get('maxHR', 0) or 0,
                activity.get('calories', 0) or 0,
                activity.get('averageRunningCadenceInStepsPerMinute', 0) or 0,
                round(activity.get('elevationGain', 0), 1) if activity.get('elevationGain') else 0,
                activity.get('activityType', {}).get('typeKey', 'running'),
                # 新增欄位
                aerobic_te,
                anaerobic_te,
                avg_step_length,
                avg_power,
                avg_temp
            ]
            
            sheet.append_row(row)
            print(f"✅ Added: {activity_date} ({row[2]} km)")
            new_entries += 1
            
        except Exception as e:
            print(f"❌ Error processing activity: {e}")
            continue
    
    print(f"\n🎉 Sync Complete! Added {new_entries} new activities.")

if __name__ == "__main__":
    main()
