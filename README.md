<p align="center">
  <img src="assets/banner.svg" alt="Forecast Sentinel" width="100%">
</p>

Pemantauan galat ramalan intensitas karbon listrik Britania Raya (data per 30 menit) dengan EWMA dan Complex Event Processing. Proyek mata kuliah Analisis Big Data.

## ALUR

<p align="center">
  <img src="assets/pipeline.svg" alt="Alur data" width="100%">
</p>

Jika API tidak tersedia, `data.py` memakai file `results/history.csv` yang sudah tersimpan.

## STATUS

| Tahap | Isi | Status |
|---|---|---|
| Fase 1 | Pengambilan data, cache, simulator stream, EDA, baseline | Selesai |
| Fase 2 | Engine EWMA dan CEP (Repetition, Trend, Absence) | Berikutnya |
| Dashboard | `app.py` | Belum dimulai |

## HASIL EDA

<p align="center">
  <img src="results/eda.png" alt="Hasil EDA galat forecast" width="100%">
</p>

| Temuan | Nilai | Implikasi |
|---|---|---|
| Galat bergerombol | Autokorelasi lag-1 ≈ 0,5 | Batas kendali dikalibrasi dari data |
| Besar galat berbeda menurut jam | Simpangan baku per jam ≈ 7 sampai 42 | Baseline per jam dipertimbangkan |
| Ada galat ekstrem | Maksimum 165, persentil ke-95 = 30 | Simpangan baku robust dipakai di samping simpangan baku biasa |

<sub>Data 10 September sampai 10 Oktober 2026.</sub>

## MULAI

Membutuhkan Python 3.10 atau lebih baru.

```bash
git clone https://github.com/imadepandu/forecast-sentinel.git
cd forecast-sentinel

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

python data.py --days 30
python eda.py
```

## STRUKTUR

```
.
├── assets/        gambar untuk README
├── data.py        ambil data, galat, cache CSV, simulator stream
├── eda.py         baseline, autokorelasi, profil per jam
├── results/       history.csv, baseline.json, eda.png
└── requirements.txt
```

## DATA

`results/history.csv`, satu baris per periode 30 menit:

| Kolom | Keterangan |
|---|---|
| `timestamp` | Awal periode (UTC) |
| `period_end` | Akhir periode (UTC) |
| `forecast` | Ramalan intensitas karbon (gCO2/kWh) |
| `actual` | Kenyataan (gCO2/kWh); kosong jika belum tersedia |
| `error` | `actual - forecast`; positif berarti ramalan terlalu rendah |

## OPSI

| Opsi | Keterangan |
|---|---|
| `--days` | Jumlah hari data (default 30) |
| `--mode` | `auto` (API, jatuh ke file jika gagal), `live` (API wajib), `csv` (hanya file tersimpan) |
| `--path` | Lokasi file riwayat (default `results/history.csv`) |
| `--reference-days` | Khusus `eda.py`: hari awal yang dijadikan referensi (default 21) |

## SUMBER DATA

[Carbon Intensity API](https://carbonintensity.org.uk), tanpa API key.