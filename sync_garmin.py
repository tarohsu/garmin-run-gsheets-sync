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
    print("🚀 Running Customized Layout Sync...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('GOOGLE_CREDENTIALS'), os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    activities = garmin.get_activities(0, 50) # 抓最近 50 筆
    
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
            
        # --- 基礎數據 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # --- 進階數據處理 ---
        
        # 1. 步幅 (Step Length)
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        # 邏輯：如果是 98.12 (cm) -> 除以100; 如果是 0.98 (m) -> 不動
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 2. 功率系列 (Power)
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # 3. 跑姿系列 (Run Dynamics)
        # GCT: 修復浮點數 (244.10006 -> 244.1)
        gct_raw = activity.get('avgGroundContactTime') or 0
        gct = round(gct_raw, 1)
        
        # 垂直振幅
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 移動效率 (Move Efficiency)
        # 嘗試抓取 'avgRunningCoefficient' 或 'runningEfficiency'
        eff_raw = (activity.get('avgRunningCoefficient') or 
                   activity.get('avgRunningEfficiency') or 
                   activity.get('avgRunningEffectiveness') or 0)
        move_eff = round(eff_raw, 1)

        # 4. 其他
        steps = activity.get('steps', 0)

        # --- 依照你的指定順序排列 (共 21 欄) ---
        row = [
            date,                                       # 1. Date
            activity.get('activityName', 'Run'),        # 2. Activity Name
            round(dist_m / 1000, 2),                    # 3. Distance (km)
            format_to_time_string(dur_s),               # 4. Duration (min) - 這裡顯示為 MM:SS
            format_pace_string(dist_m, dur_s),          # 5. Avg Pace (min/km)
            activity.get('averageHR', 0) or 0,          # 6. Avg HR
            activity.get('maxHR', 0) or 0,              # 7. Max HR
            activity.get('calories', 0) or 0,           # 8. Calories
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # 9. Avg Cadence
            round(activity.get('aerobicTrainingEffect', 0), 1),     # 10. Aerobic TE
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # 11. Anaerobic TE
            int(activity.get('elevationGain', 0) or 0), # 12. Elevation Gain (m)
            pwr_avg,                                    # 13. Avg Power (W)
            pwr_max,                                    # 14. Max Power (W)
            pwr_norm,                                   # 15. Norm Power (NP)
            step_m,                                     # 16. Avg Step Length (m)
            move_eff,                                   # 17. Move efficiency (%)
            vo_cm,                                      # 18. Vert Osc (cm)
            gct,                                        # 19. GCT (ms) [已修復]
            activity.get('activityType', {}).get('typeKey', 'run'), # 20. Activity Type
            steps                                       # 21. Total Steps
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料 (格式已優化)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
