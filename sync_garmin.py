import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

# --- 輔助函式 ---
def format_to_time_string(seconds):
    if not seconds: return "0:00"
    seconds = int(round(seconds))
    m, s = seconds // 60, seconds % 60
    return f"{m}:{s:02d}"

def format_pace_string(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds: return "0:00"
    if distance_meters <= 0: return "0:00"
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def main():
    print("🚀 啟動全地形同步 (含田徑場/越野/跑步機)...")
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    
    # 抓取 150 筆以確保覆蓋 3 個月
    activities = garmin.get_activities(0, 150)
    
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    sheet = client.open("Garmin Data").worksheet("Garmin Data")
    
    existing_dates = {row[0] for row in sheet.get_all_values()[1:] if row}
    
    target_activities = []
    
    # --- [修正] 擴充跑步類型判定 ---
    # 新增: track_running (田徑跑), virtual_run (虛擬跑), indoor_running (室內跑)
    valid_run_types = [
        'running', 
        'treadmill_running', 
        'trail_running', 
        'track_running', 
        'virtual_run',
        'indoor_running'
    ]
    
    for a in activities:
        start_time_str = a.get('startTimeLocal', '')
        if not start_time_str: continue
        
        act_date = datetime.strptime(start_time_str[:10], '%Y-%m-%d')
        if act_date < cutoff_date: continue
        
        type_key = a.get('activityType', {}).get('typeKey', '').lower()
        
        # 只要是上述任何一種跑步類型，都納入
        if type_key in valid_run_types:
            if start_time_str[:10] not in existing_dates:
                target_activities.append(a)
    
    print(f"📦 發現 {len(target_activities)} 筆新跑步資料 (含田徑跑步)...")
    
    rows_to_insert = []
    
    for activity in target_activities:
        date = activity.get('startTimeLocal', '')[:10]
        
        # --- 數據提取 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        gct = int(round(activity.get('avgGroundContactTime') or 0))
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            vr = (vo_raw / (stride_raw * 10)) * 100
        move_eff = round(vr, 1)

        gct_bal_left = activity.get('avgGroundContactBalance') or activity.get('avgGroundContactTimeBalance')
        if gct_bal_left:
            if gct_bal_left > 100: gct_bal_left /= 100
            gct_bal_str = f"{gct_bal_left}% L / {round(100 - gct_bal_left, 1)}% R"
        else:
            gct_bal_str = "--"

        vo2 = int(activity.get('vO2MaxValue') or 0)
        steps = activity.get('steps', 0)
        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)

        row = [
            date,                                       # A
            activity.get('activityName', 'Run'),        # B
            round(dist_m / 1000, 2),                    # C
            format_to_time_string(dur_s),               # D
            format_pace_string(dist_m, dur_s),          # E
            vo2,                                        # F
            activity.get('averageHR', 0) or 0,          # G
            activity.get('maxHR', 0) or 0,              # H
            round(activity.get('aerobicTrainingEffect', 0), 1),     # I
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # J
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # K
            step_m,                                     # L
            move_eff,                                   # M
            vo_cm,                                      # N
            gct,                                        # O
            gct_bal_str,                                # P
            pwr_norm,                                   # Q
            pwr_avg,                                    # R
            pwr_max,                                    # S
            int(activity.get('elevationGain', 0) or 0), # T
            min_elev,                                   # U
            max_elev,                                   # V
            steps,                                      # W
            activity.get('calories', 0) or 0            # X
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料 (含田徑/越野/室內)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
