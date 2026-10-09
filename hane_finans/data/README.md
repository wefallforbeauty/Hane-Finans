# Pakete eklenen veri

Yalnızca kamuya açık başvuru verisi. Kişisel veri bu klasöre girmez.

## tufe_tcmb_aylik.csv

Tüketici fiyat endeksinin (TÜFE) yıllık ve aylık yüzde değişimleri.
2005-01'den 2026-09'a kadar 261 ay.

- **Kaynak:** TÜİK verisi, TCMB'nin "Tüketici Fiyatları" sayfasından:
  https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Istatistikler/Enflasyon+Verileri/Tuketici+Fiyatlari
- **Alındığı tarih:** 2026-10-09.
- **Sütunlar:**
  - `ay`: YYYY-AA
  - `yillik_degisim`: önceki yılın aynı ayına göre %
  - `aylik_degisim`: önceki aya göre %
- **Kullanım:** endeks seviyeleri aylık değişimlerin zincirlenmesiyle bulunur
  ve 2025 ortalaması 100 olacak şekilde ölçeklenir (TÜİK 2025=100). Ayrıntı
  için [docs/YONTEM.md](../../docs/YONTEM.md).
- **Ne zaman kullanılır:** yalnızca internet yokken ya da `finans baslat`
  sonrasında, TÜFE henüz güncellenmemişken. `finans fiyat guncelle` daha yeni
  veriyi TCMB'den ya da EVDS'den getirir ve bu kopyanın yerine yazar.
