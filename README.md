<p align="center">
  <img src="docs/banner.svg" alt="Forecast Sentinel: pemantauan galat peramalan intensitas karbon Inggris" width="100%">
</p>

# Forecast Sentinel

Sistem pemantauan *near-real-time* untuk satu pertanyaan: **apakah ramalan intensitas karbon listrik Inggris mulai meleset dari kenyataan?**

Setiap 30 menit, [Carbon Intensity API](https://api.carbonintensity.org.uk) memberi dua angka dalam gCO2/kWh: ramalan (*forecast*) dan nilai aktual (*actual*). Selisihnya disebut **galat** (`actual − forecast`). Satu galat besar bisa muncul karena kebetulan, jadi Forecast Sentinel tidak menilai satu titik. Galat dihaluskan dengan **EWMA**, lalu pola yang bertahan dikenali dengan **CEP**.

## Alur kerja

<p align="center">
  <img src="docs/alur.svg" alt="Alur kerja: ambil data, tetapkan baseline, pantau dengan EWMA, kenali pola, tampilkan" width="100%">
</p>

## Status

| Fase | Berkas | Isi | Status |
|---|---|---|---|
| 1 | `data.py`, `eda.py` | Pengambilan data, cache, analisis eksplorasi, baseline | Selesai |
| 2 | `ewma.py`, `cep.py` | Batas kendali EWMA dan pengenalan pola | Selesai |
| 3 | `app.py` | Dashboard | Berikutnya |

## Cara menjalankan

```bash
pip install -r requirements.txt

python data.py --days 30   # ambil data dan cetak laporan kualitas
python eda.py              # baseline, autokorelasi, grafik eksplorasi
python ewma.py             # bandingkan pengaturan EWMA
python cep.py              # hitung kejadian per pola
```

Semua skrip punya opsi `--mode`:

| Mode | Perilaku |
|---|---|
| `auto` (bawaan) | Coba API, pakai cache bila API gagal |
| `live` | Wajib API |
| `csv` | Hanya cache `results/history.csv`, tanpa internet |

Jendela 30 hari mengikuti data terbaru, jadi angka bergeser sedikit setiap hari. Pakai `--mode csv` untuk mengulang hasil dari cache yang sama.

Pemakaian sebagai pustaka:

```python
from data import get_data, replay
from eda import split_reference
from cep import build_cep

reference, monitoring = split_reference(get_data(30, "csv").frame, 21)
monitor = build_cep(reference)
for event in replay(monitoring):
    detection = monitor.update(event)  # z, lcl, ucl, alarm, streak, repetition, trend, absence
```

## Istilah

| Istilah | Arti |
|---|---|
| Galat | `actual − forecast` pada satu periode 30 menit |
| Referensi | 21 hari pertama, dianggap kondisi normal |
| Pantauan | Hari-hari setelah referensi, yang diperiksa |
| Baseline | Rata-rata dan sebaran galat dari referensi |
| EWMA | Rata-rata bergerak berbobot eksponensial: galat terbaru berbobot paling besar |
| UCL / LCL | Batas kendali atas / bawah. Alarm muncul bila EWMA keluar dari batas |
| CEP | Pengolahan event berurutan untuk mengenali pola, bukan titik tunggal |

## Pengaturan dan alasannya

| Keputusan | Pilihan | Alasan |
|---|---|---|
| Garis tengah | Rata-rata referensi | EWMA adalah rata-rata, jadi secara alami bergerak ke rata-rata |
| Lebar | `robust_std` (MAD × 1,4826) | Tidak ikut membesar oleh nilai ekstrem |
| Batas | Dua sisi (UCL dan LCL) | Galat positif dan negatif sama-sama berarti peramal meleset |
| Nilai L | Dikalibrasi dari referensi | Galat berautokorelasi (lag-1 ≈ 0,5), sehingga L = 3 dari buku teks menyalakan alarm di sekitar 4–6% periode normal (simulasi), bukan 0,27% |
| Baseline | Global | Baseline per jam tidak lebih baik secara konsisten dan lebih rapuh (hanya 42 galat per jam) |
| λ dan α | 0,2 dan 5% | Dipilih dari grid 3 × 3 × 2 (`results/ewma_grid.csv`). Pada data 10 Sep – 10 Okt 2026: L ≈ 4,3, alarm pantauan 4,2% (target 5%), pergeseran 2σ0 terdeteksi sekitar 2,5 jam |
| Referensi | Dipakai utuh | Kejadian 28–30 Sep tampak seperti kegagalan peramalan sungguhan, bukan gangguan data |
| Antarmuka | Class dengan `update(event)` | Mode batch dan stream memberi hasil yang sama |

σ0 adalah `robust_std` baseline.

## Pola CEP

| Pola | Aturan | Pengaturan |
|---|---|---|
| Repetition | Alarm EWMA berturut-turut | 3 periode (1,5 jam) |
| Trend | Rata-rata galat bergulir naik atau turun terus dan sudah jauh dari normal | Jendela 4 periode, 3 langkah, jarak ≥ 1,5σ0 |
| Absence | Tidak ada data baru | Lebih dari 90 menit |

## Hasil Fase 2

Data 11 Sep – 11 Okt 2026. Format: jumlah kejadian (per hari).

| Pola | Referensi | Pantauan |
|---|---|---|
| Alarm EWMA | 15 (0,71) | 6 (0,67) |
| Repetition | 6 (0,29) | 3 (0,33) |
| Trend naik | 18 (0,86) | 6 (0,67) |
| Trend turun | 15 (0,71) | 8 (0,89) |
| Absence | 0 | 0 |

## Keterbatasan

- EWMA bisa terpancing satu lonjakan ekstrem selama beberapa periode. Dari 9 kejadian Repetition, 1 berasal dari kasus ini.
- Trend menandai arah dan momentum, bukan peringatan dini: ia menyala bersamaan dengan alarm EWMA.
- Absence baru diuji pada skenario buatan karena data tidak punya periode yang hilang.
- Referensi hanya 21 hari, jadi nilai L masih punya ketidakpastian (kisaran 3,8–4,3).

## Struktur

```
.
├── data.py          # pengambilan data, cache, laporan kualitas
├── eda.py           # baseline, autokorelasi, profil per jam, grafik
├── ewma.py          # detektor EWMA, kalibrasi batas, perbandingan pengaturan
├── cep.py           # pola Repetition, Trend, Absence
├── docs/            # banner.svg, alur.svg
├── results/         # baseline.json, eda.png, ewma_grid.csv, cep_summary.csv
├── requirements.txt
└── README.md
```