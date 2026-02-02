import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
from google.oauth2.service_account import Credentials
import gspread

# --- 使用者生理參數 (RQ 跑力計算用) ---
USER_MAX_HR = 177  # 你的最大心率
USER_REST_HR = 55  # 預設靜止心率 (若不準可自行修改)

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

def calculate_rq_strike(dist_m, dur_s, avg_hr):
    """
    推算 RQ 即時跑力
    邏輯：(配速對應的攝氧量) / (儲備心率強度 %)
    """
    if not dist_m or not dur_s or not avg_hr or avg_hr <= USER_REST_HR:
        return 0
    
    # 1. 計算速度 (公尺/分)
    speed_m_min = dist_m / (dur_s / 60)
    
    # 2. 計算配速對應的攝氧需求 (Jack Daniels 公式簡化版)
    # VO2 cost = 0.2 * speed + 3.5 (平路假設)
    vo2_cost = (0.2 * speed_m_min) + 3.5
    
    # 3. 計算強度百分比 (儲備心率法 %HRR)
    hrr_percent = (avg_hr - USER_REST_HR) / (USER_MAX_HR - USER_REST_HR)
    
    # 避免除以 0 或負數
    if hrr_percent <= 0: return 0
    
    # 4. 推算全力下的 VDOT (即跑力)
    estimated_vdot = vo2_cost / hrr_percent
    
    return round(estimated_vdot, 1)

def main():
    print("🚀 啟動 3 個月穩定同步 (含 RQ 跑力推算)...")
    
    # 1. 讀取環境變數
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not all([garmin_email, garmin_password, google_creds_json, sheet_id]):
        print("❌ 錯誤：Secrets 設定不完整")
        return

    # 2. 登入 Garmin
    try:
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        print("✅ Garmin 登入成功")
    except Exception as e:
        print(f"❌ Garmin 登入失敗: {e}")
        return

    # 3. 設定範圍
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")

    # 4. 連接 Google Sheets
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    
    existing_dates = {row[0] for row in sheet_main.get_all_values()[1:] if row}
    
    # 5. 抓取清單並過濾
    summary_list = garmin.get_activities(0, 150)
    target_activities = []
    
    for a in summary_list:
        start_time = a.get('startTimeLocal', '')
        if not start_time: continue
        
        act_date = datetime.strptime(start_time[:10], '%Y-%m-%d')
        if act_date < cutoff_date: continue
        
        if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']:
            if start_time[:10] not in existing_dates:
                target_activities.append(a)

    print(f"📦 發現 {len(target_activities)} 筆新資料...")
    rows_main = []
    
    # 6. 逐筆處理 (使用 summaryDTO 確保數據完整)
    for index, item in enumerate(target_activities):
        activity_id = item.get('activityId')
        date = item.get('startTimeLocal', '')[:10]
        name = item.get('activityName', 'Run')
        
        print(f"🔄 [{index+1}/{len(target_activities)}] 解析中: {date} - {name}...")
        
        try:
            full_data = garmin.get_activity(activity_id)
            if isinstance(full_data, str):
                full_data = json.loads(full_data)

            # === 核心：抓取 summaryDTO ===
            summary = full_data.get('summaryDTO') or full_data
            
            dist_m = summary.get('distance', 0)
            dur_s = summary.get('duration', 0)
            avg_hr = summary.get('averageHR', 0)

            # [新增] RQ 跑力計算
            rq_run_power = calculate_rq_strike(dist_m, dur_s, avg_hr)

            # 進階指標
            stride = summary.get('avgStrideLength') or 0
            step_m = round(stride / 100, 2) if stride > 10 else round(stride, 2)
            
            gct = int(round(summary.get('avgGroundContactTime') or 0))
            vo = summary.get('avgVerticalOscillation') or 0
            vo_cm = round(vo / 10, 1) if vo > 20 else round(vo, 1)
            
            vr = summary.get('avgVerticalRatio') or 0
            if vr == 0 and stride > 0 and vo > 0:
                vr = (vo / (stride * 10)) * 100
            move_eff = round(vr, 1)

            # 觸地平衡
            gct_bal = summary.get('avgGroundContactBalance')
            if gct_bal:
                if gct_bal > 100: gct_bal /= 100
                gct_bal_str = f"{gct_bal}% L / {round(100 - gct_bal, 1)}% R"
            else:
                gct_bal_str = "--"

            # 功率
            pwr_avg = int(summary.get('avgPower', 0) or summary.get('avgRunningPower', 0) or 0)
            pwr_max = int(summary.get('maxPower', 0) or 0)
            pwr_norm = int(summary.get('normPower', 0) or 0)
            
            # 其他
            steps = summary.get('steps', 0)
            min_elev = int(summary.get('minElevation') or 0)
            max_elev = int(summary.get('maxElevation') or 0)
            vo2 = int(summary.get('vO2MaxValue') or 0)
            cal = int(summary.get('calories') or 0)
            max_hr = summary.get('maxHR') or 0
            cadence = round(summary.get('averageRunningCadenceInStepsPerMinute', 0), 0)
            aerobic = round(summary.get('aerobicTrainingEffect', 0), 1)
            anaerobic = round(summary.get('anaerobicTrainingEffect', 0), 1)
            elev_gain = int(summary.get('elevationGain', 0) or 0)

            # 整理欄位 (共 25 欄, F欄插入 RQ)
            row_main = [
                date,                               # A
                name,                               # B
                round(dist_m / 1000, 2),            # C
                format_to_time_string(dur_s),       # D
                format_pace_string(dist_m, dur_s),  # E
                rq_run_power,                       # F: [新增] 推算跑力 (RQ)
                vo2,                                # G
                avg_hr,                             # H
                max_hr,                             # I
                aerobic,                            # J
                anaerobic,                          # K
                cadence,                            # L
                step_m,                             # M
                move_eff,                           # N
                vo_cm,                              # O
                gct,                                # P
                gct_bal_str,                        # Q
                pwr_norm,                           # R
                pwr_avg,                            # S
                pwr_max,                            # T
                elev_gain,                          # U
                min_elev,                           # V
                max_elev,                           # W
                steps,                              # X
                cal                                 # Y
            ]
            rows_main.append(row_main)
            time.sleep(1)

        except Exception as e:
            print(f"❌ 處理 {date} 失敗: {e}")

    # 寫入
    if rows_main:
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 成功同步 {len(rows_main)} 筆資料 (含 RQ 跑力)")
    else:
        print("✓ 資料已是最新")

if __name__ == "__main__": main()
