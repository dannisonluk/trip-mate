# 第三方資料出處與授權

本專案使用下列第三方資料集。此處的授權條款要求必須署名，因此本檔案是**必要**檔案，
不是可選文件。

---

## GeoNames — `cities5000`

| 項目 | 內容 |
|------|------|
| 來源 | <https://download.geonames.org/export/dump/> |
| 檔案 | `cities5000.zip`（另搭配 `countryInfo.txt`、`admin1CodesASCII.txt`） |
| 授權 | **Creative Commons Attribution 4.0 International（CC BY 4.0）** |
| 授權全文 | <https://creativecommons.org/licenses/by/4.0/> |
| 使用範圍 | `cities` 資料表全部內容：城市名稱、無重音名稱、國家代碼與名稱、一級行政區、市中心座標、人口、feature code |

### 署名文字

依 CC BY 4.0 第 3(a)(1) 條，呈現 GeoNames 資料處應保有署名。本專案的作法是：

1. 地圖與城市選擇介面顯示「城市資料：GeoNames（CC BY 4.0）」並連回 <https://www.geonames.org/>；
2. `docs/SECURITY.md` §2.3 說明座標來源；
3. `backend/app/models/city.py` 的模組 docstring 標明來源與授權。

### 我們做了什麼修改

CC BY 4.0 允許修改，但要求標示。相對原始 `cities5000.txt`：

- **只保留 19 欄中的 10 欄**。丟棄 `cc2`（備用國碼）、`elevation`、`dem`（對本用途無用），
  以及 `alternatenames`（每列可達 10,000 字元，會讓資料表膨脹數倍而無人讀取）。
- `country_name` 與 `admin1_name` 由代碼**查表展開**後反正規化儲存
  （分別來自 `countryInfo.txt`、`admin1CodesASCII.txt`），避免列表查詢要再 join。
- `timezone` 原樣保留但**目前無使用點**。
- **未修改**任何座標、人口或名稱值 —— `name` 與 `asciiname` 皆為原值，
  因此 GeoNames 的資料修正可以透過重新匯入（`scripts/import_cities.py`）直接套用。

### 沒有使用的資料

Google Geocoding API **刻意**未被採用。其條款限制快取不得超過 30 天、要求結果必須
顯示於 Google 地圖上、且禁止大量地理編碼以建立儲存資料集 —— 三者都與
「建立一份可離線查詢的城市參考表」直接衝突。

---

## 其他

- **字型**、**圖示**（`lucide-react`，ISC 授權）、**地圖圖磚**的授權依各自的提供者條款，
  上線前需個別確認圖磚服務的使用政策。
