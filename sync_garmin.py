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

def main():
    print("🚀 啟動雙分頁同步 (總表 + 分段)...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('GOOGLE_CREDENTIALS'), os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    activities = garmin.get_activities(0, 50) # 抓最近 50 筆
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    # 開啟主分頁 (總表)
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    
    # 開啟或建立分段分頁
    try:
        sheet_splits = client.open("Garmin Data").worksheet("Garmin Splits")
    except:
        print("⚠️ 找不到 'Garmin Splits' 分頁，請記得去 Google 試算表建立！")
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
        
        # ==========================================
        # 1. 處理主表 (Garmin Data) - 26 個欄位
        # ==========================================
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # 步幅與跑姿
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        gct = int(round(activity.get('avgGroundContactTime') or 0))
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            vr = (vo_raw / (stride_raw * 10)) * 100
        move_eff = round(vr, 1)

        gct_bal_left = activity.get('avgGroundContactBalance')
        gct_bal_str = f"{gct_bal_left}% L / {round(100 - gct_bal_left, 1)}% R" if gct_bal_left else "--"

        # 功率
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # 其他
        steps = activity.get('steps', 0)
        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)
        resp_rate = int(activity.get('avgRespirationRate') or 0)
        sweat = int(activity.get('estimatedSweatLoss') or 0)
        vo2 = int(activity.get('vO2MaxValue') or 0)

        # 依照優化後的邏輯順序 (Result -> Engine -> Tech -> Power -> Context)
        row_main = [
            date, activity_name, round(dist_m / 1000, 2), format_to_time_string(dur_s), format_pace_string(dist_m, dur_s), # A-E
            vo2, activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0, resp_rate, round(activity.get('aerobicTrainingEffect', 0), 1), round(activity.get('anaerobicTrainingEffect', 0), 1), # F-K
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), step_m, move_eff, vo_cm, gct, gct_bal_str, # L-Q
            pwr_norm, pwr_avg, pwr_max, # R-T
            int(activity.get('elevationGain', 0) or 0), min_elev, max_elev, steps, sweat, activity.get('calories', 0) or 0 # U-Z
        ]
        rows_main.append(row_main)

        # ==========================================
        # 2. 處理分段 (Garmin Splits) - 每一圈
        # ==========================================
        try:
            splits = garmin.get_activity_splits(activity_id)
            api_call_count += 1
            
            lap_count = 1
            for split in splits:
                s_dist = split.get('distance', 0)
                s_dur = split.get('duration', 0)
                
                # 分段步幅
                s_stride_raw = split.get('avgStrideLength') or 0
                s_step_m = round(s_stride_raw / 100, 2) if s_stride_raw > 10 else round(s_stride_raw, 2)
                
                # 分段功率
                s_pwr = int(split.get('avgPower') or split.get('avgRunningPower') or 0)
                
                # 分段爬升
                s_elev = int(split.get('elevationGain') or 0)

                row_split = [
                    date,                               # 日期
                    activity_name,                      # 活動名稱
                    lap_count,                          # 圈數
                    round(s_dist / 1000, 2),            # 距離
                    format_to_time_string(s_dur),       # 時間
                    format_pace_string(s_dist, s_dur),  # 配速
                    split.get('averageHR', 0) or 0,     # 平均心率
                    split.get('maxHR', 0) or 0,         # 最大心率
                    round(split.get('averageRunningCadenceInStepsPerMinute', 0), 0), # 步頻
                    s_step_m,                           # 步幅
                    s_pwr,                              # 功率
                    s_elev                              # 爬升
                ]
                rows_splits.append(row_split)
                lap_count += 1
            
            # 簡單限流
            if api_call_count >= 10:
                time.sleep(2)
                api_call_count = 0
                
        except Exception as e:
            print(f"⚠️ 無法取得分段: {e}")

    # ==========================================
    # 3. 批量寫入
    # ==========================================
    if rows_main:
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 總表已更新: 新增 {len(rows_main)} 筆活動")
    
    if rows_splits:
        sheet_splits.insert_rows(rows_splits, 2)
        print(f"✅ 分段已更新: 新增 {len(rows_splits)} 圈數據")

    if not rows_main:
        print("✓ 目前沒有新資料需要同步")

if __name__ == "__main__": main()
