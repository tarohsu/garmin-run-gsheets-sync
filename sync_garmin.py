import os
import json
import time
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

# --- [新增] 自動判讀課表類型 ---
def get_activity_type(activity):
    dur_min = (activity.get('duration', 0) or 0) / 60
    dist_km = (activity.get('distance', 0) or 0) / 1000
    hr = activity.get('averageHR', 0) or 0
    pace_sec = (activity.get('duration', 0) / dist_km) if dist_km > 0 else 0
    ana_te = activity.get('anaerobicTrainingEffect', 0) or 0
    
    # 1. LSD 系列 (時間 > 85分鐘)
    if dur_min > 85:
        # 如果無氧大於 2.0 或配速快於 5:30 (Tempo區間邊緣)，算混合LSD
        if ana_te >= 2.0 or (0 < pace_sec < 330): 
            return "混合LSD"
        return "LSD"
        
    # 2. 恢復跑 (心率 < 146, 你的E跑區間上限)
    if 0 < hr <= 146:
        return "恢復跑"
        
    # 3. 間歇系列 (看無氧 TE)
    if ana_te >= 2.3:
        if dist_km > 12: return "混合間歇" # 長距離的高強度
        return "間歇跑"
        
    # 4. Tempo (配速區間 4:45 ~ 5:20)
    # 5:20/km = 320s, 4:45/km = 285s
    if 285 <= pace_sec <= 320:
        return "Tempo"
        
    # 5. 基礎有氧 (強度高於恢復，但未達T跑)
    return "有氧跑"

def main():
    print("🚀 啟動戰情室同步 (含自動課表判讀)...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('GOOGLE_CREDENTIALS'), os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    activities = garmin.get_activities(0, 50)
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    try:
        sheet_splits = client.open("Garmin Data").worksheet("Garmin Splits")
    except:
        print("⚠️ 找不到 Splits 分頁")
        return
    
    existing_dates = {row[0] for row in sheet_main.get_all_values()[1:] if row}
    running_activities = [a for a in activities if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']]
    
    rows_main = []
    rows_splits = []
    api_call_count = 0
    
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        activity_id = activity.get('activityId')
        activity_name = activity.get('activityName', 'Run')
        
        # 執行自動判讀
        run_type = get_activity_type(activity)
        
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

        gct_bal_left = activity.get('avgGroundContactBalance')
        gct_bal_str = f"{gct_bal_left}% L / {round(100 - gct_bal_left, 1)}% R" if gct_bal_left else "--"

        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)
        resp_rate = int(activity.get('avgRespirationRate') or 0)
        sweat = int(activity.get('estimatedSweatLoss') or 0)
        vo2 = int(activity.get('vO2MaxValue') or 0)
        steps = activity.get('steps', 0)

        # --- 主表 (加入 run_type 在第三欄) ---
        row_main = [
            date, 
            activity_name, 
            run_type, # [New] 自動判讀類型
            round(dist_m / 1000, 2), format_to_time_string(dur_s), format_pace_string(dist_m, dur_s),
            vo2, activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0, resp_rate,
            round(activity.get('aerobicTrainingEffect', 0), 1), round(activity.get('anaerobicTrainingEffect', 0), 1),
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), step_m, move_eff, vo_cm, gct, gct_bal_str,
            pwr_norm, pwr_avg, pwr_max,
            int(activity.get('elevationGain', 0) or 0), min_elev, max_elev, steps, sweat, activity.get('calories', 0) or 0
        ]
        rows_main.append(row_main)

        # --- 分段表 ---
        try:
            splits = garmin.get_activity_splits(activity_id)
            api_call_count += 1
            lap_count = 1
            for split in splits:
                s_dist = split.get('distance', 0)
                s_dur = split.get('duration', 0)
                s_stride = split.get('avgStrideLength') or 0
                s_step_m = round(s_stride / 100, 2) if s_stride > 10 else round(s_stride, 2)
                s_pwr = int(split.get('avgPower') or split.get('avgRunningPower') or 0)
                s_elev = int(split.get('elevationGain') or 0)

                row_split = [
                    date, activity_name, lap_count, round(s_dist / 1000, 2),
                    format_to_time_string(s_dur), format_pace_string(s_dist, s_dur),
                    split.get('averageHR', 0) or 0, split.get('maxHR', 0) or 0,
                    round(split.get('averageRunningCadenceInStepsPerMinute', 0), 0),
                    s_step_m, s_pwr, s_elev
                ]
                rows_splits.append(row_split)
                lap_count += 1
            
            if api_call_count >= 10:
                time.sleep(2)
                api_call_count = 0
                
        except Exception as e:
            print(f"⚠️ 分段錯誤: {e}")

    if rows_main:
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 主表更新: {len(rows_main)} 筆 (含判讀)")
    
    if rows_splits:
        sheet_splits.insert_rows(rows_splits, 2)
        print(f"✅ 分段更新: {len(rows_splits)} 筆")

if __name__ == "__main__": main()
