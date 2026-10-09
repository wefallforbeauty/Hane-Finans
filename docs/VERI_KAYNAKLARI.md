# Veri kaynakları

Hangi veri nereden geliyor, neden o kaynak seçildi ve nelere dikkat etmek
gerekiyor. Durum 2026-10-09 itibarıyla.

## Kullanılanlar

| Veri | Kaynak | Anahtar | Sıklık | Programdaki modül |
|---|---|---|---|---|
| Döviz kurları (USD, EUR, …) | TCMB gösterge niteliğindeki kurlar, XML | gerekmez | iş günleri, ~15:30 | `prices/tcmb.py` |
| Döviz kurları (aralık) | TCMB EVDS3 `TP.DK.USD.A.YTL`, `TP.DK.EUR.A.YTL` | gerekir (ücretsiz) | günlük | `prices/evds.py` |
| Altın (XAU/USD) | Frankfurter v2, merkez bankalarının altın fiyatları | gerekmez | günlük | `prices/frankfurter.py` |
| TÜFE (endeks) | TCMB EVDS3 `TP.TUKFIY2025.GENEL` (2025=100) | gerekir (ücretsiz) | aylık | `prices/evds.py` |
| TÜFE (değişimler) | TCMB "Tüketici Fiyatları" sayfası | gerekmez | aylık | `prices/tcmb_cpi.py` |
| TÜFE (kopya) | Aynı tablo, pakete gömülü | — | 2026-09'a kadar | `hane_finans/data/` |

### TCMB kur bülteni

- **Adresler:**
  - Bugün: `https://www.tcmb.gov.tr/kurlar/today.xml`
  - Geçmiş bir gün: `https://www.tcmb.gov.tr/kurlar/YYYYAA/GGAAYYYY.xml`
  - Hafta sonu ve tatiller 404 döner. Program bunları atlar, sonraki
    güncellemede yeniden dener.
- **İçerik:** her döviz için `Unit` (JPY'de 100) ve dört kur: döviz alış ve
  satış, efektif alış ve satış. Program **döviz alış / Unit** değerini saklar.
- **Tarih:** bültenin kendi tarihi (`Tarih="08.10.2026"`). O gün 15:30'da
  açıklanan kur, o günün kapanış değeri sayılır.
- **Hız:** geçmiş için her gün ayrı bir istek gerekir. Bir yıl yaklaşık 250
  istek ve 1,5 dakika sürer. EVDS anahtarı varsa tek istek yeterli.

### TCMB EVDS3

- **Adres:** EVDS 2025'te `evds3.tcmb.gov.tr`'ye taşındı. API kökü
  `https://evds3.tcmb.gov.tr/igmevdsms-dis/`. Eski
  `evds2.tcmb.gov.tr/service/evds` adresi artık web arayüzüne yönleniyor.
- **İstek:** `series=KOD1-KOD2&startDate=GG-AA-YYYY&endDate=GG-AA-YYYY&type=json`.
- **Anahtar:** **`key` HTTP başlığında** gönderilmeli. Anahtarsız istek
  `403 Required request header 'key' is not present` döner (2026-10-09'da
  denendi).
- **Yanıt:** `{"totalCount": n, "items": [{"Tarih": "08-10-2026",
  "TP_DK_USD_A_YTL": "49.12670000"}, …]}`.
  - Seri kodlarındaki noktalar alt çizgiye dönüşür.
  - Değerler metin ya da `null` (tatil) olarak gelir.
  - Aylık tarih biçimi `2026-09` ya da `2026-9` olabilir.
- **Arşivlenen seriler:**
  - `TP.FG.J0`: TÜFE 2003=100, 2026-01'de bitti.
  - `TP.MK.KUL.YTL`: Kapalıçarşı külçe altın, aylık ortalama.
- **Anahtar alma:** evds3.tcmb.gov.tr'de üye olun; anahtar profil sayfasında.
  Kaydetmek için: `finans ayar evds-anahtari ANAHTAR`.
- **Henüz canlı denenmedi:** istemci anahtarla denenmedi; belgelere, anahtarsız
  isteğin döndürdüğü hataya ve başkalarının yayımladığı gerçek yanıt
  örneklerine göre yazıldı.

### Frankfurter v2 (altın)

- **Adres:** `https://api.frankfurter.dev/v2/rates?base=XAU&quotes=USD&from=…&to=…`.
- **Lisans ve maliyet:** anahtar gerektirmez, MIT lisanslı açık kaynaktır ve
  Docker ile kendi sunucunuzda da çalıştırılabilir.
- **Altın verisi:** yaklaşık on dört merkez bankası altın fiyatı yayımlıyor;
  aralarında Rusya, Polonya, Ukrayna, Romanya, Moldova, Azerbaycan ve Ermenistan
  merkez bankaları var. `providers` verilmezse API bunların son değerlerinin
  ortalamasını döndürür. 1999'dan beri günlük veri var.
- **Ortalamanın neden seçildiği:** bankalar fiyatı günün farklı saatlerinde
  yayımladığı için tek bir banka bir gün geriden gelebilir ya da günün belli
  bir anını yakalar.
  - Sakin günlerde bankalar arasındaki fark ±%1'in altında.
  - Çok oynak günlerde fark büyüyor. Örneğin 2026-01-30'da tek tek bankalar
    4 994 ile 5 586 USD/ons arasında değer verdi; ortalama 5 328.
  - Ortalama bu yüzden tek bir bankadan daha kararlı.
- **Gram altın:** TL fiyatı uluslararası paritedir: XAU/USD ÷ 31,1034768 ×
  TCMB USD/TRY.
  - Kapalıçarşı ya da banka fiyatı değildir; iç piyasadaki prim ve banka
    makası yoktur.
  - Ziynet altın ve 22 ayar için çevirme henüz yok.

### TCMB TÜFE sayfası

TÜİK'in TÜFE'sinin yıllık ve aylık değişimleri, 2005-01'den bugüne bir HTML
tablosu olarak yayımlanıyor. Program tabloyu okuyup aylık değişimleri zincirler
(bkz. [YONTEM.md](YONTEM.md) §5.2). Sayfanın yapısı değişirse program açık bir
hata verir.

## Denenen ve şimdilik seçilmeyenler

| Kaynak | Ne için | Neden şimdilik değil |
|---|---|---|
| Yahoo Finance (`yfinance`, `GC=F`, `TRY=X`) | altın, kur, BIST | Resmî değil. Deneme sırasında `429 Too Many Requests` döndü. Altın için vadeli kontrat fiyatı verir. BIST için 03'te yeniden değerlendirilecek. |
| Stooq (`xauusd`) | altın | CSV indirme artık JavaScript doğrulaması istiyor. |
| LBMA (`prices.lbma.org.uk`) | altın | Cloudflare 403. Fiyatların lisansı ICE'de. |
| EVDS `TP.MK.KUL.YTL` | gram altın | Arşivlendi, aylık ortalama. |
| Frankfurter'ın TCMB sağlayıcısı | geçmiş kurlar tek istekte | Döviz alış değil orta kur veriyor ve tarihleri bülten tarihinden bir gün kaydırıyor. Gerekirse hızlı geçmiş yükleme için seçenek olabilir. |
| `borsapy`, `borsa-mcp` | BIST, TEFAS, altın, döviz | Kapsamlı ama resmî olmayan uç noktaları kullanıyor; kişisel ve eğitim amaçlı kullanım koşulu var. 03'te veri haritası olarak kullanılacak. |
| `evdspy` | EVDS | Ağır bir paket. Aynı iş ince bir istemciyle yapıldı (`prices/evds.py`). |
| Açık bankacılık (ÖHVPS, HBHS) | banka hareketleri | Erişim yalnızca TCMB lisanslı hizmet sağlayıcılarına açık; bireylerin API anahtarı yok. Bu yüzden ekstre içe aktarılıyor. |

## Sonraki sürümlerde eklenecek kaynaklar

- **03, TEFAS fon fiyatları:** TEFAS'ın kendi JSON uç noktaları ya da
  `tefas-crawler`. TEFAS birkaç istekten sonra kısa süreli `429` döndürüyor;
  istekler aralıklı yapılmalı.
- **03, BIST hisseleri:** bir veri kaynağı seçilecek. Ücretsiz kaynaklar
  genelde 15 dakika gecikmeli.
- **03, faiz oranları:** EVDS'den politika faizi ve mevduat faizleri. Reel
  getiri kıyası ve risksiz oran için.
- **01 ve 09, TÜFE alt grupları:** EVDS'de 2025=100 COICOP grupları. Kişisel
  enflasyon için gerekli.
