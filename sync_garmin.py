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
    print("🚀 Running Ultimate Full-Metrics Sync...")
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
    
    rows_to_insert = []
    
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # --- [Phase 1] 核心數據 ---
        stride_cm = activity.get('avgStrideLength', 0)
        step_m = round(stride_cm / 100, 2) if stride_cm > 0 else 0
        pwr = int(activity.get('avgPower', 0))
        norm_pwr = int(activity.get('normPower', 0))
        temp = activity.get('avgTemperature') or activity.get('minTemperature') or 0
        steps = activity.get('steps', 0)

        # --- [Phase 2] 新增技術指標 (Run Dynamics) ---
        
        # 1. GCT (觸地時間) - 單位通常是 ms
        gct = activity.get('avgGroundContactTime') or 0
        
        # 2. 垂直振幅 (Vertical Oscillation) - 單位通常是 cm
        # API 有時給 mm (如 78), 有時給 cm (如 7.8)
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 3. 最大功率
        max_pwr = int(activity.get('maxPower', 0))

        row = [
            date,                                       # A
            activity.get('activityName', 'Run'),        # B
            round(dist_m / 1000, 2),                    # C: Distance
            format_to_time_string(dur_s),               # D: Duration
            format_pace_string(dist_m, dur_s),          # E: Pace
            activity.get('averageHR', 0) or 0,          # F: Avg HR
            activity.get('maxHR', 0) or 0,              # G: Max HR
            activity.get('calories', 0) or 0,           # H: Calories
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # I: Cadence
            int(activity.get('elevationGain', 0) or 0), # J: Elev
            activity.get('activityType', {}).get('typeKey', 'run'), # K: Type
            round(activity.get('aerobicTrainingEffect', 0), 1),     # L: Aerobic
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # M: Anaerobic
            step_m,                                     # N: Step Length
            pwr,                                        # O: Avg Power
            temp,                                       # P: Temp
            norm_pwr,                                   # Q: NP
            steps,                                      # R: Steps
            gct,                                        # S: GCT (ms) [新增!]
            vo_cm,                                      # T: Vert Osc (cm) [新增!]
            max_pwr                                     # U: Max Power [新增!]
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料 (含 GCT/VO/MaxPower)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
