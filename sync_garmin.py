import os
from garminconnect import Garmin

def main():
    print("🔬 Starting Garmin Deep Diagnosis...")
    
    # 讀取環境變數
    email = os.environ.get('GARMIN_EMAIL')
    password = os.environ.get('GARMIN_PASSWORD')
    
    if not email or not password:
        print("❌ 缺少帳號密碼，請檢查 Secrets")
        return

    try:
        # 1. 登入
        print(f"🔐 Logging in as {email}...")
        garmin = Garmin(email, password)
        garmin.login()
        
        # 2. 抓取最近 5 筆活動
        print("📡 Fetching recent activities...")
        activities = garmin.get_activities(0, 5)
        
        # 3. 尋找那場 "Tempo" 跑 (或任何有跑步數據的活動)
        target_activity = None
        for a in activities:
            if a.get('activityType', {}).get('typeKey') == 'running':
                # 優先找有功率或步幅數據的活動
                print(f"   Check: {a['activityName']} ({a['startTimeLocal']})")
                target_activity = a
                break
        
        if not target_activity:
            print("❌ 找不到跑步活動")
            return

        print(f"\n🎯 鎖定目標活動: {target_activity['activityName']} (ID: {target_activity['activityId']})")
        
        # 4. 印出 Summary 裡面的關鍵字 (確認是否真的為 0)
        print("\n--- [Level 1] Summary Data Check ---")
        for k, v in target_activity.items():
            if any(x in k.lower() for x in ['step', 'power', 'temp', 'len']):
                print(f"   {k}: {v}")

        # 5. 重頭戲：抓取 Detail (深層數據)
        print(f"\n--- [Level 2] Fetching Full Details (ID: {target_activity['activityId']}) ---")
        try:
            # 這是 Garmin 存放高階數據的地方
            full_data = garmin.get_activity(target_activity['activityId'])
            
            # 搜尋深層數據
            print("✅ Detail Fetch Success! Searching for hidden metrics...")
            found_metrics = []
            
            # 遞迴搜尋所有欄位
            def search_dict(d, path=""):
                for k, v in d.items():
                    current_path = f"{path}.{k}" if path else k
                    # 關鍵字過濾
                    if any(x in k.lower() for x in ['step', 'power', 'temp', 'run']):
                         print(f"   FOUND: {current_path} = {v}")
                    
                    if isinstance(v, dict):
                        search_dict(v, current_path)
                    elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                        # 只看列表的第一個項目，避免洗版
                        search_dict(v[0], f"{current_path}[0]")

            search_dict(full_data)
            
        except Exception as e:
            print(f"❌ 無法抓取詳細資料: {e}")

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")

if __name__ == "__main__":
    main()
