# CLAUDE.md — Freenove Computer Case Kit for Raspberry Pi

## プロジェクト概要

Freenove FNK0100 ケースキット（Raspberry Pi 5 対応）の制御ソフトウェア。
ケース内蔵のファン・LED・OLEDディスプレイを I2C 経由で制御し、電力消費量に応じた視覚フィードバックを提供する。

## リポジトリ構成

```
/
├── CLAUDE.md
├── README.md
└── Code/
    ├── task_led.py         # LEDタスク：電力連動グラデーション制御（WS2812B）
    ├── task_fan.py         # ファンタスク：温度連動PWM制御（シュミットトリガー状態機械）
    ├── task_oled.py        # OLEDタスク：6秒間隔スクリーン切替表示
    ├── task_manager.py     # 各タスクプロセスの起動・管理
    ├── api_expansion.py    # FNK0100 ハードウェア I2C 低レベルAPI
    ├── api_oled.py         # OLED ディスプレイAPI
    ├── api_systemInfo.py   # CPU温度・メモリ等システム情報取得
    ├── api_json.py         # app_config.json 読み書きラッパー
    ├── app_ui.py           # メインGUI（tkinter）
    ├── app_config.json     # 設定ファイル（LED/Fan/OLED設定を永続化）
    └── power_state.py      # Redis経由で電力消費量を取得
```

## 開発ルール

**コード修正時は必ずブランチへの commit・push・PR 作成まで行うこと。**

1. 作業用ブランチを作成: `git checkout -b claude/<説明的な名前>`
2. 変更をコミット: `git add <files> && git commit -m "<type>: <説明>"`
3. プッシュ: `git push origin HEAD`
4. PR 作成: **必ず `--repo Ken-M/Freenove_Computer_Case_Kit_for_Raspberry_Pi` を指定すること**
   ```
   gh pr create --repo Ken-M/Freenove_Computer_Case_Kit_for_Raspberry_Pi \
     --base main --head <ブランチ名> \
     --title "..." --body "..."
   ```
   - upstream（Freenove/Freenove_Computer_Case_Kit_for_Raspberry_Pi）へ誤って送らないこと
5. `main` ブランチへの直接コミットは禁止

コミットメッセージは Conventional Commits 形式（`fix:`, `feat:`, `docs:`, `refactor:` 等）を使用。

## 重要技術仕様

### ハードウェア

| 項目 | 値 |
|------|-----|
| ボード型番 | FNK0100 |
| I2C アドレス | 0x21 |
| 温度レジスタ | 0xfc（2バイト Big-Endian、生値÷1000 = ℃） |

### ファン（task_fan.py）

- **PWM 周波数**: FNK0100 = **50 Hz**（set_fan_frequency の引数は 50。50000 は誤り）
- **制御方式**: シュミットトリガー付き 4 状態機械（停止 / 低速 / 中速 / 高速）
- **ヒステリシス**: schmitt = 3℃（下方遷移の閾値は high/low_threshold - schmitt）
- 状態遷移はチャタリング防止のため上下方向で閾値が異なる

### LED（task_led.py）

- **素子**: WS2812B（RGB）
- **電力連動グラデーション**: 500W → 5000W を対数スケールで cyan → red へ変化
- **モード**: app_config.json の `LED.mode` で切替

### OLED（task_oled.py）

- **切替間隔**: 6 秒ごとに画面を自動切替

### 電力状態（power_state.py）

- Redis に格納された電力値（W）を参照

## デプロイ（Raspberry Pi 5）

```bash
cd ~/Freenove_Computer_Case_Kit_for_Raspberry_Pi
git pull
sudo systemctl restart fnk0100-led.service
sudo systemctl restart fnk0100-fan.service
sudo systemctl restart fnk0100-oled.service
```
