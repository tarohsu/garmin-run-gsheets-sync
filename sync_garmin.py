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
    pace_seconds = int(round(duration_seconds / (distance_meters / 1000)))
    return f"{pace_seconds // 60}:{pace_seconds % 60:02d}"

def safe_get(data, *keys):
    """安全地從巢狀字典中獲取數據"""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return 0
    return data if data is not None else 0

def main():
    print("🚀 啟動 FR965 旗艦全數據同步 (修復字串錯誤)...")
    
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

    # 3. 連結 Google Sheets
    creds = Credentials.from_service_account_info(json.loads(google_creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    sheet_main = client.open("Garmin Data").worksheet("Garmin Data")
    try:
        sheet_splits = client.open("Garmin Data").worksheet("Garmin Splits")
    except:
        print("❌ 找不到 'Garmin Splits' 分頁")
        return
    
    # 4. 鎖定近 3 個月活動
    # 先抓清單摘要，用來篩選日期
    summary_activities = garmin.get_activities(0, 100) 
    
    cutoff_date = datetime.now() - timedelta(days=90)
    print(f"📅 鎖定同步範圍：{cutoff_date.strftime('%Y-%m-%d')} 至今")

    existing_dates = {row[0] for row in sheet_main.get_all_values()[1:] if row}
    
    target_activities = []
    for a in summary_activities:
        start_time = a.get('startTimeLocal', '')
        if not start_time: continue
        
        act_date = datetime.strptime(start_time[:10], '%Y-%m-%d')
        if act_date < cutoff_date: continue
        
        # 只抓跑步
        if a.get('activityType', {}).get('typeKey', '').lower() in ['running', 'treadmill_running', 'trail_running']:
            # 檢查是否已存在 (避免重複抓取浪費時間)
            if start_time[:10] not in existing_dates:
                target_activities.append(a)

    print(f"📦 發現 {len(target_activities)} 筆新資料需要同步 (將逐筆抓取詳情)...")

    rows_main = []
    rows_splits = []
    
    # 5. 逐筆抓取「全數據」(Full Activity Data)
    for index, summary in enumerate(target_activities):
        activity_id = summary.get('activityId')
        date = summary.get('startTimeLocal', '')[:10]
        name = summary.get('activityName', 'Run')
        
        print(f"🔄 [{index+1}/{len(target_activities)}] 正在下載詳情: {date} - {name}...")
        
        try:
            # [關鍵修正] 使用 get_activity 而不是 get_activity_details
            # 這會回傳最完整的資料包
            full_data = garmin.get_activity(activity_id)
            
            # [防呆] 如果回傳的是字串，強制轉 JSON
            if isinstance(full_data, str):
                full_data = json.loads(full_data)

            # --- A. 提取主表數據 (從 full_data 提取更精準) ---
            # 摘要資訊通常在 'summaryDTO' 或直接在根目錄
            # 為了保險，我們優先讀取根目錄，若無則讀取 summary
            
            dist_m = full_data.get('distance', 0)
            dur_s = full_data.get('duration', 0)
            
            # 進階指標
            stride = full_data.get('avgStrideLength') or 0
            step_m = round(stride / 100, 2) if stride > 10 else round(stride, 2)
            
            # 跑姿
            gct = int(round(full_data.get('avgGroundContactTime') or 0))
            vo = full_data.get('avgVerticalOscillation') or 0
            vo_cm = round(vo / 10, 1) if vo > 20 else round(vo, 1)
            
            # 效率
            vr = full_data.get('avgVerticalRatio') or 0
            if vr == 0 and stride > 0 and vo > 0:
                vr = (vo / (stride * 10)) * 100
            move_eff = round(vr, 1)

            # [觸地平衡]
            # 注意: 這是硬體限制，若無數據就是無數據
            gct_bal = full_data.get('avgGroundContactBalance')
            if gct_bal:
                if gct_bal > 100: gct_bal /= 100
                gct_bal_str = f"{gct_bal}% L / {round(100 - gct_bal, 1)}% R"
            else:
                gct_bal_str = "--"

            # 功率
            pwr_avg = int(full_data.get('avgPower', 0) or full_data.get('avgRunningPower', 0) or 0)
            pwr_max = int(full_data.get('maxPower', 0) or 0)
            pwr_norm = int(full_data.get('normPower', 0) or 0)
            
            # [修復] 流汗量與呼吸率 (從全數據抓取)
            sweat = int(full_data.get('totalSweatLoss') or full_data.get('sweatLoss') or 0)
            resp_rate = int(full_data.get('avgRespirationRate') or 0)
            
            # 其他
            steps = full_data.get('steps', 0)
            min_elev = int(full_data.get('minElevation') or 0)
            max_elev = int(full_data.get('maxElevation') or 0)
            vo2 = int(full_data.get('vO2MaxValue') or 0)
            cal = int(full_data.get('calories') or 0)
            
            hr = full_data.get('averageHR') or 0
            max_hr = full_data.get('maxHR') or 0
            cadence = round(full_data.get('averageRunningCadenceInStepsPerMinute', 0), 0)
            aerobic = round(full_data.get('aerobicTrainingEffect', 0), 1)
            anaerobic = round(full_data.get('anaerobicTrainingEffect', 0), 1)
            elev_gain = int(full_data.get('elevationGain', 0) or 0)

            # 主表資料列
            row_main = [
                date, name, round(dist_m / 1000, 2), format_to_time_string(dur_s), format_pace_string(dist_m, dur_s),
                vo2, hr, max_hr, resp_rate, aerobic, anaerobic,
                cadence, step_m, move_eff, vo_cm, gct, gct_bal_str,
                pwr_norm, pwr_avg, pwr_max,
                elev_gain, min_elev, max_elev, steps, sweat, cal
            ]
            rows_main.append(row_main)

            # --- B. 提取分段數據 (Splits/Laps) ---
            # 在全數據模式下，laps 通常位於 'laps' 鍵中
            laps_data = full_data.get('laps', [])
            
            # 如果是字串 (雖然前面轉過了，保險起見)
            if isinstance(laps_data, str):
                laps_data = json.loads(laps_data)
                
            if laps_data:
                for i, split in enumerate(laps_data):
                    s_dist = split.get('distance', 0)
                    s_dur = split.get('duration', 0)
                    s_stride = split.get('avgStrideLength') or 0
                    s_step_m = round(s_stride / 100, 2) if s_stride > 10 else round(s_stride, 2)
                    s_pwr = int(split.get('avgPower') or split.get('avgRunningPower') or 0)
                    s_elev = int(split.get('elevationGain') or 0)
                    
                    row_split = [
                        date, name, i + 1, round(s_dist / 1000, 2),
                        format_to_time_string(s_dur), format_pace_string(s_dist, s_dur),
                        split.get('averageHR', 0) or 0, split.get('maxHR', 0) or 0,
                        round(split.get('averageRunningCadenceInStepsPerMinute', 0), 0),
                        s_step_m, s_pwr, s_elev
                    ]
                    rows_splits.append(row_split)
            else:
                print(f"⚠️ {date} 無分段資料 (Raw Keys: {list(full_data.keys())})")

            # 禮貌性延遲，避免大量請求被擋
            time.sleep(1)

        except Exception as e:
            print(f"❌ 處理 {date} 失敗: {e}")
            # 發生錯誤時，印出類型以利除錯
            # print(f"DEBUG Type: {type(full_data)}")

    # 6. 寫入 Google Sheets
    if rows_main:
        # 為了保持時間順序，我們將新抓取的資料反轉 (因為 target_activities 是新到舊，寫入時我們希望保持這順序插在最上面)
        # sheet_main.insert_rows(rows_main, 2)
        # 其實 insert_rows 會把整塊插進去，所以不需要特別反轉，直接插在 Row 2 即可
        sheet_main.insert_rows(rows_main, 2)
        print(f"✅ 主表同步完成: 新增 {len(rows_main)} 筆")
    
    if rows_splits:
        sheet_splits.insert_rows(rows_splits, 2)
        print(f"✅ 分段同步完成: 新增 {len(rows_splits)} 圈數據")

    if not rows_main:
        print("✓ 所有資料已是最新")

if __name__ == "__main__": main()
