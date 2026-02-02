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
    print("🚀 Running Logic-Optimized Sync...")
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
            
        # --- 數據提取 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # 步幅
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 功率
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # 跑姿
        gct = int(round(activity.get('avgGroundContactTime') or 0))
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 移動效率
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            vr = (vo_raw / (stride_raw * 10)) * 100
        move_eff = round(vr, 1)

        # GCT Balance
        gct_bal_left = activity.get('avgGroundContactBalance')
        if gct_bal_left:
            gct_bal_str = f"{gct_bal_left}% L / {round(100 - gct_bal_left, 1)}% R"
        else:
            gct_bal_str = "--"

        # 其他
        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)
        resp_rate = int(activity.get('avgRespirationRate') or 0)
        sweat = int(activity.get('estimatedSweatLoss') or 0)
        vo2 = int(activity.get('vO2MaxValue') or 0)
        steps = activity.get('steps', 0)

        # --- [優化邏輯] 重新排序 ---
        row = [
            # 1. 基本資訊 (Result)
            date,                                       # A: 日期
            activity.get('activityName', 'Run'),        # B: 名稱
            round(dist_m / 1000, 2),                    # C: 距離
            format_to_time_string(dur_s),               # D: 時間
            format_pace_string(dist_m, dur_s),          # E: 配速
            
            # 2. 體能引擎 (Engine)
            vo2,                                        # F: VO2 Max (移前!)
            activity.get('averageHR', 0) or 0,          # G: 平均心率
            activity.get('maxHR', 0) or 0,              # H: 最大心率
            resp_rate,                                  # I: 呼吸率
            round(activity.get('aerobicTrainingEffect', 0), 1),     # J: 有氧 TE
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # K: 無氧 TE
            
            # 3. 跑步技術 (Technique) - Sub 100 關鍵區
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # L: 步頻
            step_m,                                     # M: 步幅
            move_eff,                                   # N: 移動效率
            vo_cm,                                      # O: 垂直振幅
            gct,                                        # P: 觸地時間
            gct_bal_str,                                # Q: 觸地平衡
            
            # 4. 功率輸出 (Power)
            pwr_norm,                                   # R: NP (標準化優先)
            pwr_avg,                                    # S: 平均功率
            pwr_max,                                    # T: 最大功率
            
            # 5. 環境與消耗 (Context)
            int(activity.get('elevationGain', 0) or 0), # U: 總爬升
            min_elev,                                   # V: 最低海拔
            max_elev,                                   # W: 最高海拔
            steps,                                      # X: 總步數
            sweat,                                      # Y: 流汗量
            activity.get('calories', 0) or 0            # Z: 卡路里
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料 (邏輯排序版)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
