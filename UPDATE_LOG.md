# Güncelleme günlüğü

## 00 (2026-10-09)

İlk sürüm. Programın temeli: çift taraflı defter, kur, altın ve TÜFE verileri,
dört birimle net servet. Plan ve gerekçeler: README'deki yol haritası ve tasarım
ilkeleri.

### Yapı

- Python paketi `hane_finans`. Katmanlar:
  - `core`: saf hesaplar; veritabanı ve ağ kullanmaz
  - `ledger`: SQLite + SQLAlchemy
  - `prices`: veri kaynakları
  - `ingest`: içe aktarma
  - `reports`: raporlar
  - `cli`: komut satırı
- Komut satırı `finans` (Typer + Rich). Komutlar Türkçe; hesap ve kişi adları
  Türkçe harfsiz de yazılabilir.
- 106 pytest testi:
  - kaydedilmiş gerçek TCMB ve Frankfurter yanıtları
  - Hypothesis ile rastgele defterler
  - TÜİK'in 2005–2026 TÜFE serisiyle doğrulama
  - komut satırının uçtan uca denenmesi

### Defter

- Çift taraflı kayıt; tutarlar işaretli (varlık ve gider +, borç, gelir ve
  özkaynak −). Bir işlemin satırları her birimde toplamda sıfır olmalı. Bir
  satırın tutarı boş bırakılırsa dengeyi o satır alır.
- Tutarlar `Decimal`, veritabanında metin olarak saklanıyor; float yok. Her
  birimin ondalık sınırı var: TL 2, gram altın 4.
- Döviz, altın ve fon alım-satımı Selinger'in takas hesaplarıyla tutuluyor
  (`Özkaynak:Takas:<BİRİM>`). İşlem her birimde ayrı ayrı dengede kalıyor ve
  kur ya da fiyat etkisi sonradan ayrıştırılabiliyor.
- Hesap türleri, likidite katmanları ve borç vadesi tanımlı:
  - varlıklar: nakit, vadesiz, vadeli, döviz, altın, fon, hisse, BES…
  - borçlar: kredi kartı, KMH, ihtiyaç, konut, taşıt…
  - hesap sahibi (kişi) ya da ortak hesap
  - kurum
  - açılış ve kapanış tarihleri
- Yardımcı işlemler:
  - açılış bakiyesi (doğal işaretle)
  - harcama, gelir, transfer
  - döviz/altın değişimi (kuru nota yazar)
  - genel işlem

### Fiyatlar ve TÜFE

- **Döviz:** TCMB günlük XML bülteni, anahtarsız. Değerleme döviz alış
  kurundan yapılıyor; fiyat tarihi bülten tarihi. EVDS anahtarı varsa bir
  aralık tek istekte iniyor (`TP.DK.USD.A.YTL`, `TP.DK.EUR.A.YTL`).
- **Altın:** Frankfurter v2 XAU/USD. Yaklaşık on dört merkez bankasının
  ortalaması, 1999'dan beri, anahtarsız. Gram başına USD olarak saklanıyor;
  TL fiyatı USD/TRY ile bulunuyor.
- **TÜFE:**
  - EVDS3 `TP.TUKFIY2025.GENEL` (2025=100, anahtarlı).
  - Anahtar yoksa TCMB'nin enflasyon sayfasındaki aylık değişimler
    zincirleniyor ve 2025 ortalaması 100 olacak şekilde ölçekleniyor.
  - TCMB tablosunun bir kopyası pakete eklendi (2005-01 – 2026-09).
  - Zincirlenen endeks, yayımlanan 249 yıllık değişimin hepsini en fazla
    0,037 puan farkla (ortalama 0,008) tutturuyor.
- **EVDS3'e geçiş:** EVDS 2025'te evds3'e taşındı. Anahtar artık `key` HTTP
  başlığında gidiyor; eski `TP.FG.J0` ve `TP.MK.KUL.YTL` serileri arşivlendi.
  İstemci buna göre yazıldı.
- **Fiyat önceliği:** elle girilen fiyatı otomatik güncelleme ezmiyor. TÜFE'de
  öncelik sırası EVDS > TCMB tablosu > paketteki kopya.

### Değerleme ve raporlar

- **Net servet dört birimle:**
  - TL: öne çıkan birim
  - reel TL: son TÜFE ayının fiyatlarıyla. Günlük fiyat düzeyi, ay
    ortalarına yerleştirilen TÜFE değerleri arasında log-doğrusal
    interpolasyonla bulunuyor.
  - USD
  - gram altın
- **Fiyatlar tahmin edilmiyor:**
  - Fiyatı olmayan varlık net servete katılmıyor ve uyarı veriyor.
  - 7 günden eski fiyat "bayat" olarak işaretleniyor.
  - Her değerin yanında fiyat tarihi ve kaynağı gösteriliyor.
- **Likidite:** katmanlara göre varlıklar, kısa ve uzun vadeli borç, likit net
  varlık.
- **Kişiye göre kırılım:** `finans bilanco --kisi Babam` ya da `--kisi ortak`.
- **Raporlar:**
  - `finans servet`: net servetin aylık, haftalık ya da günlük seyri, CSV
    çıktısıyla.
  - `finans grafik`: PNG. Dört birim ayrı panellerde; tek eksen, çift eksen
    yok.

### İçe aktarma ve kurallar

- **CSV:** standart biçim ya da sütun eşlemesi.
  - Tutar tek sütunda olabilir ya da giriş ve çıkış ayrı sütunlarda.
  - Başlıktan önceki satırlar atlanabilir.
  - Tarih biçimi seçilebilir, işaret ters çevrilebilir.
  - Kodlama otomatik bulunuyor (UTF-8, Windows-1254, ISO-8859-9).
- **Mükerrer kontrolü:** her satırın bir içe aktarma anahtarı var (hesap,
  tarih, tutar, açıklama ve aynı satırların sıra numarasının özeti). Aynı ya
  da üst üste binen ekstreler iki kez eklenmiyor.
- **Ya hep ya hiç:** hatalı satır varsa hiçbir şey yazılmıyor. `--deneme` ile
  önizleme yapılabiliyor.
- **Kategori kuralları:** metin ya da regex, öncelik sırasıyla. Kategorisiz
  işlemlere sonradan da uygulanabiliyor.

### Gizlilik ve araçlar

- **Konum:** kişisel veri depo dışında (`~/.local/share/hane-finans/`); dosyalar
  0600 izinli. `.gitignore` ikinci güvenlik ağı.
- **EVDS anahtarı:** ortam değişkeninden ya da 0600 izinli ayar dosyasından
  okunuyor.
- **Yedek:** `finans yedekle`, SQLite'ın yedekleme API'siyle tutarlı bir kopya
  alıyor.
- **Örnek hane:** `finans ornek` uydurma bir haneyi ayrı bir deftere yazıyor;
  gerçek deftere yazmayı reddediyor.
- **Durum:** `finans durum` defterin özetini ve her birimde toplamın sıfır
  olduğunu gösteriyor.
