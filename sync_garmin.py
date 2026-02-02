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
    print("🚀 啟動穩定同步 (近 3 個月數據，移除呼吸率與流汗量)...")
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    
    # 增加抓取筆數以涵蓋 3 個月 (依照你的紀錄，150 筆非常足夠)
    activities = garmin.get_activities(0, 150)
    
    # 設定 3 個月 (90天) 的過濾線
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")
    
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    sheet = client.open("Garmin Data").worksheet("Garmin Data")
    
    existing_dates = {row[0] for row in sheet.get_all_values()[1:] if row}
    
    # 過濾跑步活動與日期
    target_activities = []
    for a in activities:
        start_time_str = a.get('startTimeLocal', '')
        if not start_time_str: continue
        
        act_date = datetime.strptime(start_time_str[:10], '%Y-%m-%d')
        if act_date < cutoff_date: continue
        
        if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']:
            if start_time_str[:10] not in existing_dates:
                target_activities.append(a)
    
    print(f"📦 發現 {len(target_activities)} 筆新跑步資料...")
    
    rows_to_insert = []
    
    for activity in target_activities:
        date = activity.get('startTimeLocal', '')[:10]
        
        # --- 數據提取 ---
        dist_m = activity.get('distance', 0)
        dur_s = activity.get('duration', 0)
        
        # 步幅 (Stride)
        stride_raw = activity.get('avgStrideLength') or activity.get('averageStepLength') or 0
        step_m = round(stride_raw / 100, 2) if stride_raw > 10 else round(stride_raw, 2)
        
        # 功率 (Power)
        pwr_avg = int(activity.get('avgPower', 0) or activity.get('avgRunningPower', 0) or 0)
        pwr_max = int(activity.get('maxPower', 0) or 0)
        pwr_norm = int(activity.get('normPower', 0) or 0)
        
        # 跑姿 (Dynamics)
        gct = int(round(activity.get('avgGroundContactTime') or 0))
        vo_raw = activity.get('avgVerticalOscillation') or 0
        vo_cm = round(vo_raw / 10, 1) if vo_raw > 20 else round(vo_raw, 1)
        
        # 移動效率
        vr = activity.get('avgVerticalRatio') or 0
        if vr == 0 and stride_raw > 0 and vo_raw > 0:
            vr = (vo_raw / (stride_raw * 10)) * 100
        move_eff = round(vr, 1)

        # 觸地平衡
        gct_bal_left = activity.get('avgGroundContactBalance') or activity.get('avgGroundContactTimeBalance')
        if gct_bal_left:
            if gct_bal_left > 100: gct_bal_left /= 100
            gct_bal_str = f"{gct_bal_left}% L / {round(100 - gct_bal_left, 1)}% R"
        else:
            gct_bal_str = "--"

        # 其他 (移除 resp_rate 與 sweat)
        vo2 = int(activity.get('vO2MaxValue') or 0)
        steps = activity.get('steps', 0)
        min_elev = int(activity.get('minElevation') or 0)
        max_elev = int(activity.get('maxElevation') or 0)

        # --- [優化邏輯] 重新排列順序 (移除呼吸率與流汗量，共 24 欄) ---
        row = [
            date,                                       # A: 日期
            activity.get('activityName', 'Run'),        # B: 名稱
            round(dist_m / 1000, 2),                    # C: 距離
            format_to_time_string(dur_s),               # D: 時間
            format_pace_string(dist_m, dur_s),          # E: 配速
            vo2,                                        # F: VO2 Max
            activity.get('averageHR', 0) or 0,          # G: 平均心率
            activity.get('maxHR', 0) or 0,              # H: 最大心率
            round(activity.get('aerobicTrainingEffect', 0), 1),     # I: 有氧 TE
            round(activity.get('anaerobicTrainingEffect', 0), 1),   # J: 無氧 TE
            round(activity.get('averageRunningCadenceInStepsPerMinute', 0), 0), # K: 平均步頻
            step_m,                                     # L: 平均步幅 (m)
            move_eff,                                   # M: 移動效率 (%)
            vo_cm,                                      # N: 垂直振幅 (cm)
            gct,                                        # O: 觸地時間 (ms)
            gct_bal_str,                                # P: 觸地平衡
            pwr_norm,                                   # Q: 標準化功率 (NP)
            pwr_avg,                                    # R: 平均功率 (W)
            pwr_max,                                    # S: 最大功率 (W)
            int(activity.get('elevationGain', 0) or 0), # T: 總爬升 (m)
            min_elev,                                   # U: 最低海拔 (m)
            max_elev,                                   # V: 最高海拔 (m)
            steps,                                      # W: 總步數
            activity.get('calories', 0) or 0            # X: 卡路里
        ]
        rows_to_insert.append(row)

    if rows_to_insert:
        sheet.insert_rows(rows_to_insert, 2)
        print(f"✅ 成功同步 {len(rows_to_insert)} 筆資料至主表")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
