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
    print("🚀 Running Final Fixed Sync (Vertical Ratio)...")
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
        
        # --- 進階指標 ---
        
        # 1. 步幅 (cm -> m)
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 2. 垂直振幅 (mm -> cm)
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 3. 移動效率 (即 Vertical Ratio 垂直比例)
        # 優先抓取原廠數據
        vr = activity.get('avgVerticalRatio') or 0
        # 雙重保險：如果原廠是 0，手動計算 (VO / Stride)
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            # 確保單位統一為 mm 進行計算 (stride_raw 通常是 cm, vo_raw 通常是 mm)
            stride_mm = stride_raw * 10
            vr = (vo_raw / stride_mm) * 100
        
        move_eff = round(vr, 1)
        
        # 4. GCT (ms)
        gct = round(activity.get('avgGroundContactTime') or 0, 1)
        
        # 5. 功率系列
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        steps = activity.get('steps', 0)

        row = [
            date,                                       # A
            activity.get('activityName', 'Run'),        # B
            round(dist_m / 1000, 2),                    # C
            format_to_time_string(dur_s),               # D
            format_pace_string(dist_m, dur_s),          # E
            activity.get('averageHR', 0) or 0,          # F
            activity.get('maxHR', 0) or 0,              # G
            activity.get('calories', 0) or 0,           # H
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # I
            round(activity.get('aerobicTrainingEffect', 0), 1),     # J
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # K
            int(activity.get('elevationGain', 0) or 0), # L
            pwr_avg,                                    # M
            pwr_max,                                    # N
            pwr_norm,                                   # O
            step_m,                                     # P
            move_eff,                                   # Q: Move Efficiency
            vo_cm,                                      # R
            gct,                                        # S
            activity.get('activityType', {}).get('typeKey', 'run'), # T
            steps                                       # U
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料 (含移動效率)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
