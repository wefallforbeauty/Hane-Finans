# Yöntem (sürüm 00)

Bu belge programın hesapladığı her şeyin tanımını, formülünü ve kaynağını
veriyor. Yeni bir model eklendikçe buraya bir bölüm eklenecek.

## 1. Çift taraflı defter

Bir işlem $T$, satırlardan (bacaklardan) oluşur. Her satır bir hesap $a$, bir
birim $c$ ve işaretli bir tutar $x$ taşır. Temel kural:

$$\forall c:\quad \sum_{p \in T,\; c_p = c} x_p = 0$$

İşaret kuralı (Beancount ve ledger ile aynı):

| Hesap türü | Artış | Örnek |
|---|---|---|
| Varlık | + | banka bakiyesi 25 000 → +25 000 |
| Gider | + | 450 TL market → +450 |
| Borç | − | 3 500 TL kart borcu → −3 500 |
| Gelir | − | 65 000 TL maaş → −65 000 |
| Özkaynak | − | açılış bakiyelerinin karşılığı |

Hesap $a$'nın $t$ günü sonundaki $c$ bakiyesi
$B_{a,c}(t) = \sum x_p$ (tarihi $\le t$ olan satırlar). Her işlem her birimde
sıfıra kapandığı için bütün hesaplar toplandığında her birim sıfırdır. Herhangi
bir fiyat vektörü $P(t)$ ile değerlendiğinde de toplam sıfır olur:

$$\underbrace{\sum_{a \in V \cup B}\sum_c B_{a,c}(t)\,P_c(t)}_{\text{net servet}} \;=\; -\sum_{a \in Ö \cup G \cup X}\sum_c B_{a,c}(t)\,P_c(t)$$

Burada V, B, Ö, G, X sırasıyla varlık, borç, özkaynak, gelir ve gider
hesaplarıdır. Bu özdeşlik testlerde rastgele üretilen defterlerle (Hypothesis)
sınanıyor. `finans durum` her birimde toplamın sıfır olduğunu gösterir.

Tutarlar `decimal.Decimal` olarak tutulur ve SQLite'a metin olarak yazılır:
ikili kayan nokta (0,1 + 0,2 ≠ 0,3) defterde hiç kullanılmaz. Her birimin bir
ondalık sınırı vardır (TL 2, gram altın 4).

## 2. Birden çok birim: takas hesapları

Döviz ya da altın alımı iki farklı birimi birbirine çevirir. Selinger'in
yöntemiyle her birim için bir takas hesabı (`Özkaynak:Takas:<BİRİM>`)
kullanılır; böylece işlem her birimde ayrı ayrı dengede kalır. 1 000 USD'nin
49 126,70 TL'ye alınması:

| Hesap | TRY | USD |
|---|---|---|
| Varlık:Banka:Vadesiz | −49 126,70 | |
| Özkaynak:Takas:TRY | +49 126,70 | |
| Özkaynak:Takas:USD | | −1 000 |
| Varlık:Döviz:USD | | +1 000 |

Dolar sonradan 50 TL olursa takas hesaplarının değeri
49 126,70 − 50 000 = −873,30 TL olur. Özkaynakta eksi işaret alacak demektir;
bu da 873,30 TL'lik gerçekleşmemiş kur kazancıdır. Servet değişimini "tasarruf
+ fiyat etkisi + kur etkisi" diye ayırmak (sürüm 01) bu hesaplar sayesinde
mümkün olacak.

## 3. Değerleme

- **Fiyat:** $P_c(t)$, $t$ gününe kadar bilinen en son fiyattır. Hafta sonu ve
  tatillerde son iş gününün kuru kullanılır.
- **Hangi kur?** TCMB'nin gösterge niteliğindeki **döviz alış** kuru
  (ForexBuying). Elinizdeki dövizi bankaya sattığınızda alacağınıza yakın,
  ihtiyatlı bir değerdir. Aynı kur USD biriminde raporlamada da kullanıldığı
  için 1 000 USD'lik bir hesap USD görünümünde tam 1 000 USD görünür.
- **Çapraz kur:** fiyatı doğrudan TL olarak bilinmeyen bir birim tek bir ara
  birim üzerinden çevrilir. Gram altın:

  $$P_{\text{XAU\_G}}(t) = \frac{\text{XAU/USD}(t)}{31{,}1034768} \times \text{USD/TRY}(t)$$

  31,1034768 g bir troy onstur. Altının TL fiyatı ile USD birimi aynı USD/TRY
  kurunu kullandığı için iki görünüm birbiriyle tutarlıdır:
  $NW_g = NW_{USD} \cdot 31{,}1034768 / (\text{XAU/USD})$.
- **Bayat ve eksik fiyat:** bir fiyatın yaşı, kullanılan en eski bacağın
  tarihidir. 7 günden eskiyse değer "bayat" olarak işaretlenir. Fiyatı hiç
  olmayan bir varlık **tahmin edilmez**: net servete katılmaz ve bir uyarı
  gösterilir.

## 4. Dört birimle net servet

$$NW_{TL}(t) = \sum_{a \in V \cup B}\sum_c B_{a,c}(t)\,P_c(t)$$

$$NW_{reel}(t) = NW_{TL}(t)\cdot\frac{TÜFE(b)}{TÜFE(t)} \qquad NW_{USD}(t) = \frac{NW_{TL}(t)}{P_{USD}(t)} \qquad NW_{g}(t) = \frac{NW_{TL}(t)}{P_{\text{XAU\_G}}(t)}$$

$b$ baz aydır: varsayılan olarak son yayımlanan TÜFE ayı. Reel TL, "Eylül 2026
fiyatlarıyla" gibi okunur. TL öne çıkan birimdir; diğer üçü aynı serveti
farklı ölçülerle gösterir. Yüksek enflasyonda, kur ve altın hareketlerinde bu
ölçüler birbirinden belirgin biçimde ayrışır.

## 5. TÜFE

### 5.1 Seri

TÜİK, Ocak 2026 verisinden itibaren TÜFE'yi 2025=100 temel yılıyla ve
COICOP-2018 sınıflamasıyla (13 ana grup) yayımlıyor. Geçmiş veri yeni temele
taşındı; aylık ve yıllık değişim oranları değişmedi. EVDS'deki güncel seri
`TP.TUKFIY2025.GENEL` (2005-01'den). Eski `TP.FG.J0` (2003=100) Ocak 2026'da
bitti ve arşivlendi.

### 5.2 Anahtarsız yol: zincirleme

TCMB'nin sayfası her ay için yıllık ve aylık yüzde değişimi verir. Endeks
seviyesi aylık değişimlerin zincirlenmesiyle kurulur ve 2025 ortalaması 100
olacak şekilde ölçeklenir:

$$I_m = I_{m-1}\left(1 + \frac{r_m}{100}\right), \qquad \frac{1}{12}\sum_{m \in 2025} I_m = 100$$

**Doğruluk.** Yayımlanan aylık değişimler 0,01 puana yuvarlanmıştır, yani her
ayın hatası en fazla ±0,005 puandır. Bu hatanın etkisi doğrudan ölçüldü:
zincirlenen endeksten hesaplanan yıllık değişimler, TCMB'nin yayımladığı 249
yıllık değişimin hepsini **en fazla 0,037 puan, ortalama 0,008 puan** farkla
tutturuyor (`tests/test_inflation.py`). Ocak 2026 için aylık %4,84 ve yıllık
%30,65 aynen çıkıyor. EVDS anahtarı varsa TÜİK'in resmî seviyeleri kullanılır.

### 5.3 Farklı temellerin eklenmesi

Aynı zincir endeksin iki farklı temeli arasında sabit bir oran vardır. Eski
bir seri yenisine eklenecekse oran, ortak aylardaki (en az 6 ay) oranların
ortalaması olarak bulunur; ortak aylarda yeni seri geçerlidir
(`core.inflation.splice`).

### 5.4 Günlük fiyat düzeyi

Aylık TÜFE ayın ortalama fiyat düzeyini ölçer, bu yüzden ayın ortasına
yerleştirilir: $t_m$ = ayın ilk günü $+ (n_m - 1)/2$ gün. Aradaki günlerin
düzeyi log-doğrusal interpolasyonla bulunur:

$$\ln P(t) = (1-w)\ln I_m + w \ln I_{m+1}, \qquad w = \frac{t - t_m}{t_{m+1} - t_m}$$

- **Serinin başından önce:** ilk ayın değeri kullanılır.
- **Son ayın ortasından sonra:** son değer sabit tutulur. Sonrasındaki
  enflasyon henüz bilinmez ve bu durum raporda ayrıca belirtilir.

Enflasyona endeksli tahvillerin referans endeksi de (örneğin ABD TIPS, 31 CFR
356 Ek B) aylık TÜFE'yi günlere doğrusal olarak dağıtır. Orada ödeme
hesaplandığı için iki-üç aylık bir gecikme vardır. Burada amaç alım gücünü
ölçmek olduğundan gecikme kullanılmaz.

Fark büyük değil ama yüksek enflasyonda önemli. 31 Ağustos 2026'daki bir
değer Eylül fiyatlarına çevrilirken:

- interpolasyon çarpanı 1,0093 verir;
- "her gün ayın kendi endeksini kullanır" yöntemi 1,0184 verir.

Bu ikinci yöntem `--tufe-yontemi aylik` ile seçilebilir.

### 5.5 Fisher denklemi

Reel faiz ya da reel getiri $(1+i)/(1+\pi) - 1$ (Fisher 1930). Bu sürümde
yalnızca fonksiyon olarak var; borç (02) ve yatırım (03) modüllerinde
kullanılacak.

## 6. Likidite

| Katman | Anlamı | Varsayılan hesap türleri |
|---|---|---|
| 0 Anında | aynı gün kullanılabilir | nakit, vadesiz, döviz |
| 1 Kısa sürede | ≤ 1 hafta, piyasa fiyatından | vadeli mevduat, altın, fon, hisse |
| 2 Kısıtlı | çekilebilir ama bedeli var | BES, alacaklar |
| 3 Likit değil | aylar sürer | gayrimenkul, araç, diğer |

Her hesabın katmanı elle değiştirilebilir (`--likidite`). Borçlar iki vadeye
ayrılır:

- **Kısa vadeli:** kredi kartı, KMH, kişilere borç.
- **Uzun vadeli:** krediler.

**Likit net varlık** = katman 0 ve 1'deki varlıklar − kısa vadeli borçlar.

Hane finansında likidite oranı (likit varlık / aylık zorunlu gider; 3–6 ay
önerisi) DeVaney (1994) ve Greninger ve ark. (1996) ile yerleşmiştir. Oranın
kendisi aylık giderler hesaplandığında sürüm 01'de eklenecek.

## 7. İçe aktarma

Her satırın bir içe aktarma anahtarı vardır:

$$k = \text{SHA-256}\big(\text{hesap} \,|\, \text{tarih} \,|\, \text{tutar} \,|\, \text{açıklama} \,|\, n\big)$$

- Hesap ve açıklama büyük-küçük harf ve Türkçe harflerden arındırılır.
- Tutar normalleştirilir (450,00 ile 450 aynı sayılır).
- $n$, aynı dosyadaki birebir aynı satırların sıra numarasıdır.

Bunun sonuçları:

- Aynı gün iki eş alışveriş ikisi birden kaydedilir.
- Aynı dosya ikinci kez aktarıldığında hiçbir şey eklenmez.
- Üst üste binen iki dökümdeki ortak satırlar bir kez sayılır.

**Sınır:** banka bir satırın açıklamasını iki dökümde farklı yazarsa satır yeni
sanılır. İleride bakiye kontrolleri ve yaklaşık eşleştirme eklenecek.

Kategori kuralları büyük-küçük harf ve Türkçe harf farkını yok sayar. Kurallar
önceliğe göre denenir ve ilk uyan kural uygulanır.

## Kaynakça

- Campbell, J. Y. (2006). Household finance. *Journal of Finance*, 61(4), 1553–1604.
- DeMiguel, V., Garlappi, L., & Uppal, R. (2009). Optimal versus naive diversification: How inefficient is the 1/N portfolio strategy? *Review of Financial Studies*, 22(5), 1915–1953.
- DeVaney, S. A. (1994). The usefulness of financial ratios as predictors of household insolvency: Two perspectives. *Financial Counseling and Planning*, 5, 5–24.
- Ellerman, D. P. (1985). The mathematics of double entry bookkeeping. *Mathematics Magazine*, 58(4), 226–233.
- Fisher, I. (1930). *The Theory of Interest*. New York: Macmillan.
- Greninger, S. A., Hampton, V. L., Kitt, K. A., & Achacoso, J. A. (1996). Ratios and benchmarks for measuring the financial well-being of families and individuals. *Financial Services Review*, 5(1), 57–70.
- Michaud, R. O. (1989). The Markowitz optimization enigma: Is "optimized" optimal? *Financial Analysts Journal*, 45(1), 31–42.
- Selinger, P. *Tutorial on multiple currency accounting*. https://www.mathstat.dal.ca/~selinger/accounting/tutorial.html
- TÜİK. Tüketici Fiyat Endeksi, 2025=100 temel yılına geçiş duyuruları (2025–2026).
- U.S. Department of the Treasury. 31 CFR Part 356, Appendix B (enflasyona endeksli menkul kıymetlerde referans TÜFE).
