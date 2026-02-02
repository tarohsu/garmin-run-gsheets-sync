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
    print("🚀 啟動最終修正版同步 (修復分頁與空白欄位)...")
    garmin_email, garmin_password = os.environ.get('GARMIN_EMAIL'), os.environ.get('GARMIN_PASSWORD')
    google_creds_json, sheet_id = os.environ.get('SHEET_ID') # 注意: 有些人是設 SHEET_ID, 有些是 GOOGLE_SHEET_ID, 請確認
    # 修正: 讀取 GOOGLE_CREDENTIALS
    if not google_creds_json:
        google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')

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
        print("⚠️ 找不到 'Garmin Splits' 分頁")
        return
    
    existing_dates = {row[0] for row in sheet_main.get_all_values()[1:] if row}
    # 只抓跑步
    running_activities = [a for a in activities if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']]
    
    rows_main = []
    rows_splits = []
    
    api_call_count = 0
    
    for activity in running_activities:
        date = activity.get('startTimeLocal', '')[:10]
        if date in existing_dates: continue
            
        activity_id = activity.get('activityId')
        activity_name = activity.get('activityName', 'Run')
        
        # --- [主表] 數據提取 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # 步幅
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 跑姿
        gct = int(round(activity.get('avgGroundContactTime') or 0))
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 移動效率
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            vr = (vo_raw / (stride_raw * 10)) * 100
        move_eff = round(vr, 1)

        # [修復] 觸地平衡
        # 嘗試多個 Key: avgGroundContactBalance, avgGroundContactTimeBalance
        # 有些回傳 50.5, 有些回傳 5050 (需除以100)
        gct_bal_raw = activity.get('avgGroundContactBalance') or activity.get('avgGroundContactTimeBalance')
        if gct_bal_raw:
            # 如果數值大於 100 (例如 5050), 轉成 50.5
            if gct_bal_raw > 100: gct_bal_raw /= 100
            
            # Garmin 通常回傳的是 "左腳" 的百分比
            gct_bal_str = f"{gct_bal_raw}% L / {round(100 - gct_bal_raw, 1)}% R"
        else:
            gct_bal_str = "--"

        # 功率
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # [修復] 流汗量
        sweat = int(activity.get('estimatedSweatLoss') or activity.get('sweatLoss') or activity.get('totalSweatLoss') or 0)

        # 其他
        steps = activity.get('steps', 0)
        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)
        resp_rate = int(activity.get('avgRespirationRate') or 0)
        vo2 = int(activity.get('vO2MaxValue') or 0)
        cal = int(activity.get('calories') or 0)

        # 26 欄 (已移除 '課表類型')
        row_main = [
            date, activity_name, round(dist_m / 1000, 2), format_to_time_string(dur_s), format_pace_string(dist_m, dur_s), # A-E
            vo2, activity.get('averageHR', 0) or 0, activity.get('maxHR', 0) or 0, resp_rate, round(activity.get('aerobicTrainingEffect', 0), 1), round(activity.get('anaerobicTrainingEffect', 0), 1), # F-K
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), step_m, move_eff, vo_cm, gct, gct_bal_str, # L-Q
            pwr_norm, pwr_avg, pwr_max, # R-T
            int(activity.get('elevationGain', 0) or 0), min_elev, max_elev, steps, sweat, cal # U-Z
        ]
        rows_main.append(row_main)

        # --- [分頁] 抓取詳細分圈數據 (Laps) ---
        try:
            # 改用 get_activity_details，這包含了 laps, splits, metrics
            details = garmin.get_activity_details(activity_id)
            api_call_count += 1
            
            # 優先找 'splits' (每公里)，如果沒有則找 'laps' (手動/自動計圈)
            # 注意: 詳情裡的 key 可能是 'activityDetailMetrics' 或 'laps'
            # 觀察 Garmin JSON 結構，通常 'laps' 比較穩
            splits_data = details.get('laps', [])
            
            # 如果 laps 也是空的，嘗試找 splits
            if not splits_data:
                splits_data = details.get('splits', [])

            lap_count = 1
            for split in splits_data:
                s_dist = split.get('distance', 0)
                s_dur = split.get('duration', 0)
                
                # 步幅
                s_stride = split.get('avgStrideLength') or 0
                s_step_m = round(s_stride / 100, 2) if s_stride > 10 else round(s_stride, 2)
                
                # 功率
                s_pwr = int(split.get('avgPower') or split.get('avgRunningPower') or 0)
                
                # 爬升
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
            
            # 限流保護
            if api_call_count >= 10:
                time.sleep(2)
                api_call_count = 0
                
        except Exception as e:
            print(f"⚠️ 無法取得 {date} 的分段詳情: {e}")

    # 批量寫入
    if rows_main:
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 總表已更新: 新增 {len(rows_main)} 筆 (欄位已修正)")
    
    if rows_splits:
        sheet_splits.insert_rows(rows_splits, 2)
        print(f"✅ 分段已更新: 新增 {len(rows_splits)} 圈數據")

    if not rows_main:
        print("✓ 目前沒有新資料")

if __name__ == "__main__": main()
