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
    print("🚀 Running Final Chinese Layout Sync...")
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
        
        # 1. 步幅 (cm -> m)
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 2. 功率系列
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # 3. 跑姿系列
        # GCT: 改為整數 (int)
        gct_raw = activity.get('avgGroundContactTime') or 0
        gct = int(round(gct_raw))
        
        # 垂直振幅
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 移動效率 (垂直比例)
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            stride_mm = stride_raw * 10
            vr = (vo_raw / stride_mm) * 100
        move_eff = round(vr, 1)

        # 4. 其他
        steps = activity.get('steps', 0)

        # --- 排列順序 (移除 Activity Type) ---
        row = [
            date,                                       # 1. 日期
            activity.get('activityName', 'Run'),        # 2. 活動名稱
            round(dist_m / 1000, 2),                    # 3. 距離 (km)
            format_to_time_string(dur_s),               # 4. 時間 (min)
            format_pace_string(dist_m, dur_s),          # 5. 平均配速
            activity.get('averageHR', 0) or 0,          # 6. 平均心率
            activity.get('maxHR', 0) or 0,              # 7. 最大心率
            activity.get('calories', 0) or 0,           # 8. 卡路里
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # 9. 平均步頻
            round(activity.get('aerobicTrainingEffect', 0), 1),     # 10. 有氧 TE
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # 11. 無氧 TE
            int(activity.get('elevationGain', 0) or 0), # 12. 總爬升 (m)
            pwr_avg,                                    # 13. 平均功率 (W)
            pwr_max,                                    # 14. 最大功率 (W)
            pwr_norm,                                   # 15. 標準化功率 (NP)
            step_m,                                     # 16. 平均步幅 (m)
            move_eff,                                   # 17. 移動效率 (%)
            vo_cm,                                      # 18. 垂直振幅 (cm)
            gct,                                        # 19. 觸地時間 (ms)
            steps                                       # 20. 總步數 (原本在最後，現在遞補上來)
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料 (中文配置)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
