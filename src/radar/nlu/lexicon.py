"""Multilingual lexicon for the rule-based parser.

Patterns are regular expressions matched case-insensitively against the raw
text. Cyrillic, Armenian and Georgian are inflected, so most entries are stems
followed by \\w*. Coverage: en, ru, hy, ka, kk, de, fr, it, es. Mixed-language
input works because every pattern is tried regardless of the UI language.
"""

from __future__ import annotations

from radar.domain.common import Domain

W = r"\w*"

# Domain cues, weighted by how unambiguous they are.
DOMAIN_CUES: dict[Domain, list[tuple[str, float]]] = {
    Domain.DINING: [
        (r"\brestaura\w*", 3),
        (r"\bristorant\w*", 3),
        (r"\btrattori\w*", 3),
        (r"\bcaf[eé]s?\b", 2),
        (r"\bbistro\w*", 2),
        (r"\bbrunch\w*", 2),
        (r"\bdinner\b", 2),
        (r"\blunch\b", 2),
        (r"\bpubs?\b", 2),
        (r"\bbakery\b", 2),
        (r"\bbars?\b", 1.5),
        (r"\beat\b", 1),
        (r"\bsushi\b", 2),
        (r"\bpizz\w*", 2),
        (r"ресторан" + W, 3),
        (r"\bкафе\b", 2),
        (r"ужин" + W, 2),
        (r"обед" + W, 1.5),
        (r"поесть", 1.5),
        (r"суши", 2),
        (r"пиццер" + W, 2),
        (r"мейрамхана" + W, 3),
        (r"тамақтан" + W, 1),
        (r"ռեստորան" + W, 3),
        (r"սրճարան" + W, 2),
        (r"ընթրիք" + W, 2),
        (r"ճաշ" + W, 1.5),
        (r"რესტორან" + W, 3),
        (r"კაფე" + W, 2),
        (r"ვახშამ" + W, 2),
        (r"სადილ" + W, 1.5),
        (r"\babendessen\b", 2),
        (r"\bmittagessen\b", 1.5),
        (r"\bessen gehen\b", 2),
        (r"\bkneipe\b", 2),
        (r"\bdîner\b", 2),
        (r"\bdéjeuner\b", 1.5),
        (r"\bboulangerie\b", 2),
        (r"\bcena\b", 2),
        (r"\bpranzo\b", 1.5),
        (r"\bpanetteria\b", 2),
        (r"\bcomida\b", 1),
        (r"\balmuerzo\b", 1.5),
        (r"\bpanader[ií]a\b", 2),
        (r"\bcuisine\b", 1),
        (r"\bküche\b", 1),
        (r"\bcucina\b", 1),
    ],
    Domain.STAY: [
        (r"\bhotels?\b", 3),
        (r"\bhôtel\w*", 3),
        (r"\bhostel\w*", 3),
        (r"\baparthotel\w*", 3),
        (r"\bguest ?house\w*", 3),
        (r"\bholiday (apartment|rental|home)s?\b", 3),
        (r"\baccommodation\b", 3),
        (r"\bplace to stay\b", 3),
        (r"\bcheck[- ]?in\b", 1.5),
        (r"\bnights?\b", 1.5),
        (r"\bapartment\b", 1),
        (r"\bairbnb\b", 2),
        (r"\bbed and breakfast\b|\bb&b\b", 3),
        (r"\bqueen[- ]size\b|\bking[- ]size\b", 2),
        (r"отел" + W, 3),
        (r"гостиниц" + W, 3),
        (r"хостел" + W, 3),
        (r"апартамент" + W, 1.5),
        (r"переночева" + W, 2),
        (r"ноч(ь|и|ей)\b", 1.5),
        (r"қонақ ?үй" + W, 3),
        (r"հյուրանոց" + W, 3),
        (r"հյուրատուն" + W, 3),
        (r"სასტუმრო" + W, 3),
        (r"სასტუმრ" + W, 3),
        (r"\bunterkunft\b", 3),
        (r"\bferienwohnung\w*", 3),
        (r"\bpension\b", 2),
        (r"\bübernachtung\w*", 2),
        (r"\bhébergement\b", 3),
        (r"\blogement\b", 2),
        (r"\balbergo\b", 3),
        (r"\balloggio\b", 3),
        (r"\balojamiento\b", 3),
        (r"\bhospedaje\b", 3),
        (r"\bnoches?\b", 1.5),
        (r"\bnotti\b", 1.5),
        (r"\bnuits?\b", 1.5),
        (r"\bnächte\b", 1.5),
    ],
    Domain.REAL_ESTATE: [
        (r"\blong[- ]term\b", 3),
        (r"\bmonthly rent\b", 3),
        (r"\brent(al)?\b.*\bmonth", 2),
        (r"\blease\b", 2),
        (r"\bfor sale\b", 3),
        (r"\bbuy (an? )?(apartment|flat|house|home)\b", 3),
        (r"\blandlord\b", 2),
        (r"\bbedrooms?\b", 1),
        (r"\bflat\b", 1),
        (r"\bstudio flat\b", 2),
        (r"долгосрочн" + W, 3),
        (r"на длительн" + W, 3),
        (r"снять квартир" + W, 2.5),
        (r"аренд" + W, 1.5),
        (r"купить (квартир|дом)" + W, 3),
        (r"квартир" + W, 1),
        (r"\bв месяц\b", 1.5),
        (r"пәтер" + W, 1.5),
        (r"բնակարան" + W, 1.5),
        (r"վարձակալ" + W, 2),
        (r"ბინა" + W, 1.5),
        (r"ქირავდება|ქირით", 2),
        (r"\blangfristig\w*", 3),
        (r"\bmietwohnung\b", 3),
        (r"\bkaltmiete\b|\bwarmmiete\b", 3),
        (r"\blongue durée\b", 3),
        (r"\bloyer\b", 2),
        (r"\blungo termine\b", 3),
        (r"\baffitto\b", 2),
        (r"\blargo plazo\b", 3),
        (r"\balquiler\b", 2),
    ],
    Domain.FLIGHT: [
        (r"\bflights?\b", 3),
        (r"\bfly\b", 2),
        (r"\bplane\b", 2),
        (r"\bairlines?\b", 2),
        (r"\bairfare\b", 3),
        (r"рейс" + W, 3),
        (r"перел[её]т" + W, 3),
        (r"самол[её]т" + W, 2),
        (r"авиабилет" + W, 3),
        (r"ұшақ" + W, 3),
        (r"әуе ?билет" + W, 3),
        (r"թռիչք" + W, 3),
        (r"ավիատոմս" + W, 3),
        (r"ինքնաթիռ" + W, 2),
        (r"ფრენ" + W, 3),
        (r"ავიაბილეთ" + W, 3),
        (r"თვითმფრინავ" + W, 2),
        (r"\bflug\w*", 3),
        (r"\bvols?\b", 3),
        (r"\bvoli?\b", 3),
        (r"\bvuelos?\b", 3),
        (r"\bavion\b", 2),
    ],
    Domain.RAIL: [
        (r"\btrains?\b", 3),
        (r"\brail\w*", 2),
        (r"\b(ice|tgv|frecciarossa|railjet|ave)\b", 3),
        (r"\bnight train\b|\bsleeper\b", 3),
        (r"поезд" + W, 3),
        (r"электричк" + W, 3),
        (r"пойыз" + W, 3),
        (r"գնացք" + W, 3),
        (r"მატარებელ" + W, 3),
        (r"\bzug\b|\bzüge\b|\bbahn\b", 3),
        (r"\btreno\b|\btreni\b", 3),
        (r"\btren(es)?\b", 3),
        (r"\bnachtzug\b", 3),
    ],
    Domain.GROUND: [
        (r"\b(bus|buses|coach)\b", 3),
        (r"\bferr(y|ies)\b", 3),
        (r"\bairport transfer\b", 3),
        (r"\btaxi\b", 2),
        (r"\bcar rental\b|\brent a car\b|\bhire car\b", 3),
        (r"\bway to (get|travel)\b", 2.5),
        (r"\bhow (do i|to) get\b", 2.5),
        (r"\bcombinations? of\b", 1),
        (r"автобус" + W, 3),
        (r"паром" + W, 3),
        (r"трансфер" + W, 2.5),
        (r"такси", 2),
        (r"как (добраться|доехать)", 2.5),
        (r"ավտոբուս" + W, 3),
        (r"ավտոբուս", 3),
        (r"ავტობუს" + W, 3),
        (r"\bfernbus\w*", 3),
        (r"\bfähre\b", 3),
        (r"\bmietwagen\b", 3),
        (r"\bautobus\b", 3),
        (r"\btraghett\w*", 3),
        (r"\bautobús\b", 3),
        (r"\bautocar\b", 3),
        (r"\bbateau\b", 2),
    ],
    Domain.EVENT: [
        (r"\bconcerts?\b", 3),
        (r"\bfestivals?\b", 3),
        (r"\bcinema\b|\bmovies?\b", 3),
        (r"\btheat(er|re)\b", 3),
        (r"\bmuseums?\b", 3),
        (r"\bexhibitions?\b", 3),
        (r"\bmatch(es)?\b|\bgame tickets?\b", 2),
        (r"\bguided tours?\b|\btours?\b", 2),
        (r"\bescape rooms?\b", 3),
        (r"\bkarting\b|\bgo[- ]?karts?\b", 3),
        (r"\bamusement park\b|\btheme park\b", 3),
        (r"\bski(ing)?\b", 2.5),
        (r"\bhik(e|ing)\b", 2.5),
        (r"\bchristmas markets?\b", 3),
        (r"\bworkshops?\b", 2),
        (r"\bactivit(y|ies)\b", 1.5),
        (r"\bvirtual reality\b|\bvr\b", 2.5),
        (r"\bthings to do\b", 2),
        (r"концерт" + W, 3),
        (r"фестивал" + W, 3),
        (r"кино" + W, 2.5),
        (r"театр" + W, 3),
        (r"музе" + W, 3),
        (r"выставк" + W, 3),
        (r"экскурси" + W, 3),
        (r"квест" + W, 2.5),
        (r"картинг", 3),
        (r"համերգ" + W, 3),
        (r"թատրոն" + W, 3),
        (r"թանգարան" + W, 3),
        (r"ցուցահանդես" + W, 3),
        (r"კონცერტ" + W, 3),
        (r"თეატრ" + W, 3),
        (r"მუზეუმ" + W, 3),
        (r"გამოფენ" + W, 3),
        (r"\bkonzert\w*", 3),
        (r"\bausstellung\w*", 3),
        (r"\bweihnachtsm(a|ä)rkt\w*", 3),
        (r"\bkino\b", 3),
        (r"\bmusée\b", 3),
        (r"\bspectacle\b", 3),
        (r"\bconcerto\b|\bconcierto\b", 3),
        (r"\bmuseo\b", 3),
        (r"\bmostra\b", 2.5),
        (r"\bexposición\b", 3),
        (r"\bteatro\b", 3),
    ],
    Domain.WELLNESS: [
        (r"\bspas?\b", 3),
        (r"\bmassage\w*", 3),
        (r"\bsauna\w*", 2.5),
        (r"\bhammam\b", 3),
        (r"\bbeauty salon\b|\bnail\w* salon\b|\bmanicure\b", 3),
        (r"\bbarbers?\b|\bhaircut\b", 3),
        (r"\byoga\b|\bpilates\b|\bfitness class\w*", 3),
        (r"\bcowork\w*", 3),
        (r"\bphotographer\b", 3),
        (r"\bcleaning service\b|\bcleaner\b", 3),
        (r"\bpet (grooming|boarding)\b", 3),
        (r"\brepair\b", 2),
        (r"\bappointment\b", 1.5),
        (r"\bспа\b", 3),
        (r"массаж" + W, 3),
        (r"саун" + W, 2.5),
        (r"барбер" + W, 3),
        (r"салон красоты", 3),
        (r"маникюр", 3),
        (r"коворкинг", 3),
        (r"մերսում" + W, 3),
        (r"սպա", 3),
        (r"მასაჟ" + W, 3),
        (r"სპა", 3),
        (r"\bfriseur\w*", 3),
        (r"\bmassaggio\b|\bmasaje\b", 3),
        (r"\bcoiffeur\b", 3),
        (r"\bpeluquer[ií]a\b", 3),
        (r"\bparrucchiere\b", 3),
    ],
    Domain.SHOPPING: [
        (r"\bin stock\b", 3),
        (r"\bback in stock\b", 3),
        (r"\bprice of\b", 2),
        (r"\bused\b.*\bfor sale\b", 2),
        (r"\bsecond[- ]hand\b", 3),
        (r"\bcollectibles?\b", 3),
        (r"\bjob(s| vacanc\w*)\b", 3),
        (r"\bdeals?\b", 1.5),
        (r"\bstore\b|\bshop\b", 1.5),
        (r"\bbuy (a|an|the)?\s?(?!apartment|flat|house)\w+", 1.5),
        (r"\bproduct\b", 2),
        (r"в наличии", 3),
        (r"б/у", 3),
        (r"ваканси" + W, 3),
        (r"магазин" + W, 1.5),
        (r"\bgebraucht\w*", 3),
        (r"\bauf lager\b", 3),
        (r"\bd'occasion\b", 3),
        (r"\busato\b", 3),
        (r"\bde segunda mano\b", 3),
    ],
    Domain.PLAN: [
        (r"\bplan (a|my|an|the)\b", 3),
        (r"\bitinerary\b", 3),
        (r"\bweekend (in|getaway|trip)\b", 2.5),
        (r"\b(\w+-day|day) trip\b", 2.5),
        (r"\bcity trip\b|\bcity break\b", 3),
        (r"\bbusiness trip\b", 3),
        (r"\bfamily holiday\b|\bfamily vacation\b", 3),
        (r"\blayover\b", 2.5),
        (r"\bbirthday\b", 2),
        (r"\bevening date\b|\bdate night\b", 3),
        (r"\bgetaway\b", 2),
        (r"сплан" + W, 3),
        (r"сплань" + W, 3),
        (r"маршрут" + W, 2),
        (r"выходны" + W, 1.5),
        (r"\bplane (ein|eine|einen|mein)\b", 3),
        (r"\breiseplan\b", 3),
        (r"\bplanifie\w*", 3),
        (r"\bpianific\w*", 3),
        (r"\bplanific\w*", 3),
        (r"\bweek-?end\b", 1),
    ],
}

# Category tokens. Order matters only for readability.
CATEGORY_CUES: list[tuple[str, str]] = [
    # cuisine
    (r"\bsushi\b|суши|սուշի|სუში", "cuisine:sushi"),
    (r"\bpizz\w*|пицц\w*|պիցց\w*|პიც\w*", "cuisine:pizza"),
    (r"\bitalian\b|итальянск\w*|\bitalienisch\w*|\bitalien(ne)?\b|\bitaliana?\b", "cuisine:italian"),
    (r"\bgeorgian\b|грузинск\w*|վրացական|ქართულ\w*|\bgeorgisch\w*|\bgéorgien\w*|\bgeorgian[ao]\b", "cuisine:georgian"),
    (r"\barmenian\b|армянск\w*|հայկական|სომხურ\w*|\barmenisch\w*|\barménien\w*|\barmen[ao]\b", "cuisine:armenian"),
    (
        r"\bjapanese\b|японск\w*|ճապոնական|იაპონურ\w*|\bjapanisch\w*|\bjaponais\w*|\bgiappones\w*|\bjapones\w*",
        "cuisine:japanese",
    ),
    (
        r"\bchinese\b|китайск\w*|չինական|ჩინურ\w*|\bchinesisch\w*|\bchinois\w*|\bcines\w*|\bchin[ao]\b",
        "cuisine:chinese",
    ),
    (r"\bindian\b|индийск\w*|\bindisch\w*|\bindien(ne)?\b|\bindian[ao]\b|\bindi[ao]\b", "cuisine:indian"),
    (r"\bfrench\b|французск\w*|\bfranzösisch\w*|\bfrançaise?\b|\bfrancese\b|\bfrancés\b", "cuisine:french"),
    (r"\bthai\b|тайск\w*|\bthailändisch\w*|\bthaï\w*|\btailandes\w*", "cuisine:thai"),
    (r"\bmexican\b|мексиканск\w*|\bmexikanisch\w*|\bmexicain\w*|\bmessican\w*|\bmexican[ao]\b", "cuisine:mexican"),
    (r"\bburgers?\b|бургер\w*|բուրգեր\w*", "cuisine:burger"),
    (r"\bsteak\w*|стейк\w*", "cuisine:steak_house"),
    (
        r"\bseafood\b|морепродукт\w*|\bmeeresfrüchte\b|\bfruits de mer\b|\bfrutti di mare\b|\bmariscos\b",
        "cuisine:seafood",
    ),
    (r"\bramen\b|рамен", "cuisine:ramen"),
    (r"\bkebab\w*|кебаб\w*|шашлык\w*|խորոված\w*|\bkhorovats\b|\bbbq\b|\bbarbecue\b", "cuisine:barbecue"),
    (r"\bkhinkali\b|хинкали|խինկալի|ხინკალ\w*|\bkhachapuri\b|хачапури|ხაჭაპურ\w*", "cuisine:georgian"),
    (r"\bvegan restaurant\b", "cuisine:vegan"),
    # dining place types
    (r"\bcaf[eé]s?\b|\bкафе\b|սրճարան\w*|კაფე\w*|\bcaffè\b|\bcafetería\b", "place:cafe"),
    (r"\bbars?\b|\bбар\b|\bбары\b|ბარ\w*", "place:bar"),
    (r"\bpubs?\b|\bпаб\w*|\bkneipe\b", "place:pub"),
    (r"\bbaker(y|ies)\b|пекарн\w*|\bbäckerei\b|\bboulangerie\b|\bpanetteria\b|\bpanader[ií]a\b", "place:bakery"),
    (r"\bbrunch\w*", "place:brunch"),
    # wellness
    (r"\bspas?\b|\bспа\b|\bսպա\b|სპა", "place:spa"),
    (r"\bmassage\w*|массаж\w*|մերսում\w*|მასაჟ\w*|\bmassaggio\b|\bmasaje\b", "place:massage"),
    (r"\bsauna\w*|саун\w*|\bbanya\b|\bбаня\b|\bhammam\b", "place:sauna"),
    (r"\bbeauty salon\b|салон красоты|\bnail\w* salon\b|\bmanicure\b|маникюр", "place:beauty"),
    (
        r"\bbarbers?\b|барбер\w*|\bhaircut\b|\bfriseur\w*|\bcoiffeur\b|\bparrucchiere\b|\bpeluquer[ií]a\b",
        "place:hairdresser",
    ),
    (r"\bfitness\b|\bgym\b|\byoga\b|\bpilates\b|фитнес|йога", "place:fitness"),
    (r"\bcowork\w*|коворкинг", "place:coworking"),
    (r"\bpet grooming\b|груминг", "place:pet_grooming"),
    (r"\bpet boarding\b|передержк\w*", "place:pet_boarding"),
    (r"\bphotographer\b|фотограф\w*", "place:photographer"),
    (r"\brepair\b|ремонт\w*", "place:repair"),
    # events
    (r"\bconcerts?\b|концерт\w*|համերգ\w*|კონცერტ\w*|\bkonzert\w*|\bconcerto\b|\bconcierto\b", "event:concert"),
    (r"\bfestivals?\b|фестивал\w*|\bfestival\b", "event:festival"),
    (r"\bcinema\b|\bmovies?\b|кино\w*|\bkino\b|\bcinéma\b|\bcine\b", "event:cinema"),
    (r"\btheat(er|re)\b|театр\w*|թատրոն\w*|თეატრ\w*|\bthéâtre\b|\bteatro\b", "event:theatre"),
    (r"\bmuseums?\b|музе\w*|թանգարան\w*|მუზეუმ\w*|\bmusée\b|\bmuseo\b", "event:museum"),
    (
        r"\bexhibitions?\b|выставк\w*|ցուցահանդես\w*|გამოფენ\w*|\bausstellung\w*|\bexposition\b|\bmostra\b|"
        r"\bexposición\b",
        "event:exhibition",
    ),
    (r"\bmatch(es)?\b|\bfootball\b|\bfootball game\b|\bfußball\b|\bfutbol\b|\bcalcio\b", "event:sports"),
    (r"\bguided tours?\b|экскурси\w*|\bführung\b|\bvisite guidée\b|\bvisita guidata\b|\bvisita guiada\b", "event:tour"),
    (r"\bescape rooms?\b|квест\w*", "event:escape_room"),
    (r"\bvirtual reality\b|\bvr\b", "event:vr"),
    (r"\bkarting\b|\bgo[- ]?karts?\b|картинг", "event:karting"),
    (r"\bamusement park\b|\btheme park\b|парк аттракционов|\bfreizeitpark\b", "event:amusement_park"),
    (r"\bski(ing)?\b|лыж\w*|горнолыжн\w*", "event:ski"),
    (r"\bhik(e|ing)\b|поход\w*|\bwander\w*|\brandonnée\b|\bescursion\w*|\bsenderismo\b", "event:hiking"),
    (
        r"\bchristmas markets?\b|рождественск\w* ярмарк\w*|\bweihnachtsm(a|ä)rkt\w*|\bmarché de noël\b|"
        r"\bmercatin\w* di natale\b|\bmercado navideño\b",
        "event:christmas_market",
    ),
    (r"\bworkshops?\b|мастер-класс\w*|\bmasterclass\b", "event:workshop"),
    # stays
    (r"\bhostel\w*|хостел\w*", "property:hostel"),
    (r"\baparthotel\w*", "property:aparthotel"),
    (r"\bguest ?house\w*|гостев\w* дом\w*|հյուրատուն\w*|\bpension\b", "property:guesthouse"),
    (r"\bholiday (apartment|rental|home)s?\b|\bferienwohnung\w*|апартамент\w*", "property:apartment"),
    (r"\bhotels?\b|отел\w*|гостиниц\w*|հյուրանոց\w*|სასტუმრო\w*|\bhôtel\w*|\balbergo\b", "property:hotel"),
    # real estate
    (r"\bhouse\b|\bдом\b|\bhaus\b|\bmaison\b|\bcasa\b", "property:house"),
    (r"\bshared room\b|\bкомнат[ау] в\b|\bwg[- ]zimmer\b|\bcolocation\b", "property:room"),
    (r"\bstudent housing\b|\bstudent\w* (dorm|residence)\b|общежити\w*", "property:student"),
    (r"\boffice\b|\bcommercial\b|\bофис\w*|коммерческ\w*", "property:commercial"),
    (
        r"\bapartment\b|\bflat\b|квартир\w*|բնակարան\w*|ბინა\w*|\bwohnung\b|\bappartement\b|\bappartamento\b|"
        r"\bpiso\b|\bapartamento\b|пәтер\w*",
        "property:apartment",
    ),
    # ground modes
    (r"\bferr(y|ies)\b|паром\w*|\bfähre\b|\btraghett\w*|\bferry\b", "mode:ferry"),
    (
        r"\b(bus|buses|coach)\b|автобус\w*|ավտոբուս\w*|ავტობუს\w*|\bfernbus\w*|\bautobus\b|\bautobús\b|\bautocar\b",
        "mode:bus",
    ),
    (r"\bairport transfer\b|трансфер\w*", "mode:transfer"),
    (r"\bcar rental\b|\brent a car\b|\bhire car\b|аренд\w* авто\w*|\bmietwagen\b", "mode:car_rental"),
    (r"\btaxi\b|такси", "mode:taxi"),
    (
        r"\bcombinations? of\b|\bany (mode|way)\b|\bway to (get|travel)\b|\bhow (do i|to) get\b|"
        r"как (добраться|доехать)",
        "mode:multimodal",
    ),
]

# Constraint cues: (pattern, constraint key, value). Value None -> True.
CONSTRAINT_CUES: list[tuple[str, str, object]] = [
    (
        r"\boutdoor seating\b|\bterrace\b|\bpatio\b|\boutside\b|веранд\w*|террас\w*|летн\w* площадк\w*|"
        r"\bterrasse\b|\bdraußen\b|\ben terrasse\b|\ball'aperto\b|\bterrazza\b|\bal aire libre\b|\bterraza\b",
        "outdoor_seating",
        None,
    ),
    (r"\bindoor seating\b|\binside\b", "indoor_seating", None),
    (r"\bprivate (dining|room)\b|отдельн\w* (зал|кабинет)\w*|\bséparée\b|\bsalle privée\b", "private_dining", None),
    (
        r"\bvegetarian\b|вегетариан\w*|բուսակեր\w*|\bvegetarisch\w*|\bvégétarien\w*|\bvegetarian[ao]\b|"
        r"\bvegetarian[oa]s?\b",
        "diet.vegetarian",
        None,
    ),
    (r"\bvegan\w*|веган\w*|\bvégan\w*", "diet.vegan", None),
    (r"\bhalal\b|халял\w*|ハラル", "diet.halal", None),
    (r"\bkosher\b|кошерн\w*|\bkoscher\b|\bcasher\b", "diet.kosher", None),
    (
        r"\bgluten[- ]free\b|без глютена|\bglutenfrei\b|\bsans gluten\b|\bsenza glutine\b|\bsin gluten\b",
        "diet.gluten_free",
        None,
    ),
    (
        r"\bwheelchair\b|\baccessible\b|\bstep[- ]free\b|инвалидн\w* коляск\w*|\bbarrierefrei\w*|"
        r"\baccessible aux fauteuils\b|\baccessibile\b|\baccesible\b",
        "wheelchair",
        None,
    ),
    (r"\bparking\b|парковк\w*|\bparkplatz\w*|\bparcheggio\b|\baparcamiento\b|\bestacionamiento\b", "parking", None),
    (
        r"\bpet[- ]friendly\b|\bpets? allowed\b|\bwith (my|a|our) (dog|cat)\b|с (собак|животн|питом)\w*|"
        r"\bhaustiere erlaubt\b|\bhundefreundlich\b|\banimaux acceptés\b|\banimali ammessi\b|"
        r"\bse admiten mascotas\b",
        "pet_friendly",
        None,
    ),
    (
        r"\bkid[- ]friendly\b|\bchild[- ]friendly\b|\bfamily[- ]friendly\b|\bwith kids\b|с детьми|детск\w* меню|"
        r"\bkinderfreundlich\b|\bpour enfants\b|\bper bambini\b|\bpara niños\b",
        "child_friendly",
        None,
    ),
    (r"\b(free )?wi-?fi\b|\binternet\b|вай-?фай|\bwlan\b", "wifi", None),
    (
        r"\bair[- ]?con\w*|\ba/?c\b|кондиционер\w*|\bklimaanlage\b|\bclimatisation\b|\baria condizionata\b|"
        r"\baire acondicionado\b",
        "air_conditioning",
        None,
    ),
    (r"\bqueen[- ]?size\w*|\bqueen bed\b", "bed.queen", None),
    (r"\bking[- ]?size\w*|\bking bed\b", "bed.king", None),
    (
        r"\bdouble bed\b|двуспальн\w* кроват\w*|\bdoppelbett\b|\blit double\b|\bletto matrimoniale\b|"
        r"\bcama de matrimonio\b",
        "bed.double",
        None,
    ),
    (r"\btwin beds?\b|раздельн\w* кроват\w*", "bed.twin", None),
    (
        r"\bprivate bath(room)?\b|\bensuite\b|\ben-suite\b|собственн\w* ванн\w*|\beigenes bad\b|"
        r"\bsalle de bain privée\b|\bbagno privato\b|\bbaño privado\b",
        "private_bathroom",
        None,
    ),
    (r"\bbath ?tub\b|ванн(а|ой)\b|\bbadewanne\b|\bbaignoire\b|\bvasca\b|\bbañera\b", "bathtub", None),
    (r"\bbreakfast\b|завтрак\w*|\bfrühstück\b|\bpetit[- ]déjeuner\b|\bcolazione\b|\bdesayuno\b", "breakfast", None),
    (r"\bkitchen\w*|кухн\w*|\bküche\b|\bcuisine équipée\b|\bcucina\b|\bcocina\b", "kitchen", None),
    (r"\bbalcon(y|ies)\b|балкон\w*|պատշգամբ\w*|აივან\w*|\bbalkon\b|\bbalcon\b|\bbalcone\b|\bbalcón\b", "balcony", None),
    (r"\bheating\b|отоплени\w*|\bheizung\b|\bchauffage\b|\briscaldamento\b|\bcalefacción\b", "heating", None),
    (r"\b(swimming )?pool\b|бассейн\w*|\bschwimmbad\b|\bpiscine\b|\bpiscina\b", "pool", None),
    (
        r"\blate check-?in\b|поздн\w* заезд\w*|\bspäter check-?in\b|\barrivée tardive\b|\bcheck-in tardivo\b|"
        r"\btrain arrives at midnight\b|\barriv\w* (at|around|after) midnight\b",
        "late_checkin",
        None,
    ),
    (r"\bearly check-?in\b|ранн\w* заезд\w*", "early_checkin", None),
    (
        r"\bfree cancellation\b|бесплатн\w* отмен\w*|\bkostenlose stornierung\b|\bannulation gratuite\b|"
        r"\bcancellazione gratuita\b|\bcancelación gratuita\b",
        "free_cancellation",
        None,
    ),
    (r"\bpay at (the )?property\b|оплат\w* на месте", "pay_at_property", None),
    (r"\bentire (place|apartment|home)\b|целиком", "entire_place", None),
    (r"\bfurnished\b|меблирован\w*|с мебелью|\bmöbliert\b|\bmeublé\b|\barredato\b|\bamueblado\b", "furnished", None),
    (r"\belevator\b|\blift\b|лифт\w*|\baufzug\b|\bascenseur\b|\bascensore\b|\bascensor\b", "elevator", None),
    (
        r"\bdirect\b|\bnon[- ]?stop\b|без пересад\w*|прям\w* (рейс|поезд)\w*|\bdirekt\w*|\bdirecte?\b|"
        r"\bdiretto\b|\bdirecto\b|\bsin escalas\b|\bsans escale\b|\bohne umsteigen\b",
        "direct",
        None,
    ),
    (r"\bchecked (bag|baggage|luggage)\b|\bwith (luggage|baggage)\b|с багаж\w*|\baufgabegepäck\b", "checked_bag", None),
    (
        r"\brefundable\b|возвратн\w*|\berstattbar\b|\bremboursable\b|\brimborsabile\b|\breembolsable\b",
        "refundable",
        None,
    ),
    (r"\bchangeable\b|\bflexible ticket\b|\bflex\b", "changeable", None),
    (
        r"\bfirst class\b|перв\w* класс\w*|\b1\.? klasse\b|\bpremière classe\b|\bprima classe\b|\bprimera clase\b",
        "first_class",
        None,
    ),
    (r"\bsleeper\b|\bnight train\b|\bnachtzug\b|спальн\w* вагон\w*|\bcouchette\b", "sleeper", None),
    (r"\b(with|take) (a |my )?bike\b|\bbicycle\b|велосипед\w*|\bfahrrad\b|\bvélo\b|\bbici\b", "bicycle", None),
    (r"\bindoors?\b|в помещени\w*|\bdrinnen\b", "indoor", None),
    (r"\boutdoors?\b|на открытом воздухе|\bim freien\b|\ben plein air\b", "outdoor", None),
    (r"\bsauna\b", "sauna", None),
    (
        r"\bfemale (massage )?(therapist|masseuse|staff|master|specialist)s?\b|\bwoman therapist\b|"
        r"\bженщин\w* (массажист|мастер)\w*|\bмассажистк\w*|\bmasseurin\w*|\bmasseuse\b",
        "staff.female",
        None,
    ),
    (r"\bmale (massage )?(therapist|staff)s?\b|\bмассажист\b(?!к)", "staff.male", None),
    (
        r"\bin stock\b|\bavailable again\b|\bback in stock\b|в наличии|\bauf lager\b|\ben stock\b|"
        r"\bdisponibile\b",
        "in_stock",
        None,
    ),
    (
        r"\bused\b|\bsecond[- ]hand\b|б/у|\bgebraucht\w*|\bd'occasion\b|\busato\b|\bde segunda mano\b",
        "condition.used",
        None,
    ),
    (
        r"\breservations?\b|\bbook a table\b|\btable for\b|забронир\w* стол\w*|столик\w*|\btisch\b|\btable\b|"
        r"\btavolo\b|\bmesa\b",
        "reservations",
        None,
    ),
    (
        r"\btakeaway\b|\btake[- ]out\b|на вынос|\bzum mitnehmen\b|\bà emporter\b|\bda asporto\b|\bpara llevar\b",
        "takeaway",
        None,
    ),
]

ATMOSPHERE_CUES: list[tuple[str, str]] = [
    (
        r"\bromantic\w*|романтич\w*|ռոմանտիկ\w*|რომანტიკ\w*|романтикалық|\bromantisch\w*|\bromantique\b|"
        r"\bromantic[oa]\b|\bromántic[oa]\b",
        "atmosphere.romantic",
    ),
    (
        r"\bcozy\b|\bcosy\b|уютн\w*|\bgemütlich\w*|\bcosy\b|\bchaleureux\b|\baccogliente\b|\bacogedor\w*",
        "atmosphere.cozy",
    ),
    (
        r"\bluxur\w*|\bupscale\b|\bfine dining\b|роскошн\w*|люкс\w*|премиальн\w*|\bluxuriös\w*|\bde luxe\b|"
        r"\blussuos\w*|\blujos\w*",
        "atmosphere.luxury",
    ),
    (r"\bcasual\b|непринужд\w*|\blässig\b|\bdécontracté\b", "atmosphere.casual"),
    (r"\bfamily\b|семейн\w*|\bfamilien\w*|\bfamilial\w*|\bfamiliar\b", "atmosphere.family"),
]

PREFERENCE_MARKERS = (
    r"\bpreferabl[ey]\b|\bprefer(red|ably|s)?\b|\bideally\b|\bif possible\b|\bnice to have\b|\bwould be nice\b|"
    r"\bbonus\b|\boptional(ly)?\b|желательно|предпочтительно|по возможности|хорошо бы|было бы неплохо|"
    r"ցանկալի|նախընտրել\w*|სასურველი\w*|сасурвели|мүмкіндігінше|қалауынша|\bvorzugsweise\b|\bam liebsten\b|"
    r"\bidealerweise\b|\bwenn möglich\b|\bde préférence\b|\bidéalement\b|\bsi possible\b|\bpreferibilmente\b|"
    r"\bpossibilmente\b|\bse possibile\b|\bpreferiblemente\b|\bde preferencia\b|\bsi es posible\b"
)

MONITOR_CUES = (
    r"\bnotify\b|\balert me\b|\blet me know\b|\bwatch\b|\bkeep (looking|checking|an eye)\b|\btrack\b|"
    r"\bping me\b|уведом\w*|сообщи\w*|следи\w*|оповест\w*|дай знать|ծանուցիր|տեղեկացրու|"
    r"შემატყობინე|მაცნობე|хабарла\w*|\bbenachrichtig\w*|\bsag mir bescheid\b|\binformier\w*|"
    r"\bpréviens\w*|\bavertis\w*|\bavvisami\b|\bavvisa\w*|\bavísame\b|\bnotifíca\w*"
)

NEW_RESULTS_CUES = (
    r"\bnew listings?\b|\bwhenever .* appears?\b|\bas soon as .* appears?\b|\bwhen .* (appears?|posted)\b|"
    r"\bsuitable (apartment|flat|listing) appears\b|нов\w* объявлени\w*|как только появит\w*|\bneue anzeige\w*|"
    r"\bnouvelle annonce\b|\bnuovo annuncio\b|\bnuevo anuncio\b"
)

AVAILABILITY_CUES = (
    r"\bbecomes? available\b|\bavailable again\b|\bback in stock\b|\bkeep looking until\b|\bfree up\b|"
    r"\bbecome available\b|освободит\w*|появит\w* (в наличии|мест\w*)|станет доступ\w*|\bwieder verfügbar\b|"
    r"\bfrei wird\b|\bdevient disponible\b|\btorna disponibile\b|\bvuelva a estar disponible\b"
)

SORT_CUES: list[tuple[str, str]] = [
    (
        r"\bcheapest\b|\bcheap\b|\bbudget\b|самы\w* дешёв\w*|самы\w* дешев\w*|дешев\w*|дешёв\w*|\bgünstigst\w*|"
        r"\bbillig\w*|\bmoins cher\b|\bpiù economic\w*|\bmás barat\w*|ամենաէժան\w*|ყველაზე იაფ\w*",
        "cheapest",
    ),
    (
        r"\bfastest\b|\bquickest\b|самы\w* быстр\w*|\bschnellst\w*|\ble plus rapide\b|\bpiù veloce\b|"
        r"\bmás rápid\w*",
        "fastest",
    ),
    (
        r"\bclosest\b|\bnearest\b|\bnearby\b|ближайш\w*|рядом|\bnächst\w*|\ble plus proche\b|\bpiù vicin\w*|"
        r"\bmás cercan\w*",
        "closest",
    ),
    (
        r"\bbest[- ]rated\b|\btop[- ]rated\b|\bhighest[- ]rated\b|\bhighly rated\b|лучш\w* рейтинг\w*|"
        r"\bbestbewertet\w*|\bmieux not\w*|\bmeglio recensit\w*|\bmejor valorad\w*",
        "rated",
    ),
    (r"\bearliest\b|\bas early as\b|самы\w* ранн\w*|\bfrühest\w*", "earliest"),
    (r"\bbest value\b|\bvalue for money\b|цена.?качество|\bpreis-leistung\w*", "value"),
]

LONG_TERM_CUES = (
    r"\blong[- ]term\b|\bmonthly\b|\bper month\b|\b/month\b|\ba month\b|\bmo\b|долгосрочн\w*|на длительн\w*|"
    r"в месяц|ամսական|ყოველთვიურ\w*|айына|\blangfristig\w*|\bpro monat\b|\bmonatlich\w*|\blongue durée\b|"
    r"\bpar mois\b|\blungo termine\b|\bal mese\b|\blargo plazo\b|\bal mes\b|\bmensual\w*"
)
SALE_CUES = r"\bfor sale\b|\bbuy\b|\bpurchase\b|купить|продаж\w*|\bkaufen\b|\bacheter\b|\bcomprare\b|\bcomprar\b"

NEAR_ANCHORS: list[tuple[str, str]] = [
    (
        r"\b(train|railway|main|central) station\b|\bвокзал\w*|\bhauptbahnhof\b|\bhbf\b|\bbahnhof\b|\bgare\b|"
        r"\bstazione\b|\bestación\b|կայարան\w*|სადგურ\w*",
        "near:station",
    ),
    (
        r"\bairport\b|аэропорт\w*|\bflughafen\b|\baéroport\b|\baeroporto\b|\baeropuerto\b|օդանավակայան\w*|"
        r"აეროპორტ\w*|әуежай\w*",
        "near:airport",
    ),
    (
        r"\b(city )?cent(er|re)\b|\bcentral\b|\bdowntown\b|\bold town\b|центр\w*|\bzentrum\b|\baltstadt\b|"
        r"\bcentre[- ]ville\b|\bcentro\b|կենտրոն\w*|ცენტრ\w*",
        "near:center",
    ),
]

PLAN_KIND_CUES: list[tuple[str, str]] = [
    (
        r"\bromantic weekend\b|романтическ\w* выходн\w*|\bromantisches wochenende\b|\bweek-?end romantique\b|"
        r"\bweekend romantico\b|\bfin de semana romántico\b",
        "romantic_weekend",
    ),
    (
        r"\b(\d|three|two|four|five)[- ]day (city )?trip\b|\bcity (trip|break)\b|\bstädtereise\b|\bcitytrip\b",
        "city_trip",
    ),
    (
        r"\bbusiness trip\b|командировк\w*|\bgeschäftsreise\b|\bvoyage d'affaires\b|\bviaggio di lavoro\b|"
        r"\bviaje de negocios\b",
        "business_trip",
    ),
    (r"\bfamily (holiday|vacation|trip)\b|семейн\w* отдых\w*|\bfamilienurlaub\b", "family_holiday"),
    (r"\blayover\b|\bstopover\b|пересадк\w*|\bumstieg\w*|\bescale\b|\bscalo\b", "layover"),
    (r"\bbirthday\b|день рождения|\bgeburtstag\b|\banniversaire\b|\bcompleanno\b|\bcumpleaños\b", "birthday"),
    (r"\bdate night\b|\bevening date\b|\bromantic evening\b|свидани\w*|романтическ\w* вечер\w*", "evening_date"),
    (
        r"\bday trip\b|однодневн\w*|\btagesausflug\b|\bexcursion d'une journée\b|\bgita di un giorno\b|"
        r"\bexcursión de un día\b",
        "day_trip",
    ),
    (r"\bweekend\b|выходн\w*|\bwochenende\b|\bfine settimana\b|\bfin de semana\b", "romantic_weekend"),
]

# Small cardinal numbers. Used for party size, bedrooms, nights.
NUMBER_WORDS: dict[str, int] = {
    # en
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "a couple": 2,
    "couple": 2,
    # ru
    "один": 1,
    "одного": 1,
    "одна": 1,
    "одной": 1,
    "два": 2,
    "две": 2,
    "двух": 2,
    "двое": 2,
    "двоих": 2,
    "три": 3,
    "трёх": 3,
    "трех": 3,
    "трое": 3,
    "троих": 3,
    "четыре": 4,
    "четырёх": 4,
    "четырех": 4,
    "четверо": 4,
    "четверых": 4,
    "пять": 5,
    "пятерых": 5,
    "пятеро": 5,
    "шесть": 6,
    "шестерых": 6,
    # kk
    "бір": 1,
    "екі": 2,
    "үш": 3,
    "төрт": 4,
    "бес": 5,
    # hy
    "մեկ": 1,
    "երկու": 2,
    "երեք": 3,
    "չորս": 4,
    "հինգ": 5,
    # ka
    "ერთი": 1,
    "ორი": 2,
    "სამი": 3,
    "ოთხი": 4,
    "ხუთი": 5,
    # de
    "ein": 1,
    "eine": 1,
    "einen": 1,
    "eins": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    # fr
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    # it
    "uno": 1,
    "una": 1,
    "due": 2,
    "tre": 3,
    "quattro": 4,
    "cinque": 5,
    "sei": 6,
    # es
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
}

CURRENCY_TOKENS: dict[str, str] = {
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "евро": "EUR",
    "եվրո": "EUR",
    "ევრო": "EUR",
    "$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "доллар": "USD",
    "долларов": "USD",
    "доллара": "USD",
    "դոլար": "USD",
    "დოლარი": "USD",
    "dólares": "USD",
    "dollari": "USD",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "фунт": "GBP",
    "фунтов": "GBP",
    "֏": "AMD",
    "amd": "AMD",
    "dram": "AMD",
    "drams": "AMD",
    "драм": "AMD",
    "драмов": "AMD",
    "դրամ": "AMD",
    "₾": "GEL",
    "gel": "GEL",
    "lari": "GEL",
    "лари": "GEL",
    "ლარი": "GEL",
    "₸": "KZT",
    "kzt": "KZT",
    "tenge": "KZT",
    "тенге": "KZT",
    "теңге": "KZT",
    "chf": "CHF",
    "franc": "CHF",
    "francs": "CHF",
    "franken": "CHF",
    "франк": "CHF",
    "франков": "CHF",
    "pln": "PLN",
    "zł": "PLN",
    "zloty": "PLN",
    "złoty": "PLN",
    "злотых": "PLN",
    "czk": "CZK",
    "kč": "CZK",
    "крон": "CZK",
    "₽": "RUB",
    "rub": "RUB",
    "руб": "RUB",
    "рублей": "RUB",
    "рубля": "RUB",
    "azn": "AZN",
    "₼": "AZN",
    "manat": "AZN",
    "uzs": "UZS",
    "kgs": "KGS",
    "uah": "UAH",
    "₴": "UAH",
    "гривен": "UAH",
    "mdl": "MDL",
    "sek": "SEK",
    "nok": "NOK",
    "dkk": "DKK",
    "huf": "HUF",
    "ft": "HUF",
    "ron": "RON",
    "bgn": "BGN",
    "rsd": "RSD",
    "try": "TRY",
    "₺": "TRY",
    "aed": "AED",
    "jpy": "JPY",
    "¥": "JPY",
    "cny": "CNY",
    "isk": "ISK",
}

BUDGET_MAX_MARKERS = (
    r"\bmax(imum)?\b|\bup to\b|\bunder\b|\bbelow\b|\bless than\b|\bno more than\b|\bwithin\b|\bbudget\b|"
    r"\bat most\b|\bcheaper than\b|\bnot over\b|\bfor\b|\bдо\b|\bне дороже\b|\bмаксимум\b|\bв пределах\b|"
    r"\bбюджет\w*|\bдешевле\b|\bниже\b|\bmaximal\b|\bbis\b|\bhöchstens\b|\bunter\b|\bjusqu'à\b|\bmoins de\b|"
    r"\bfino a\b|\bmeno di\b|\bmassimo\b|\bhasta\b|\bmenos de\b|\bmáximo\b|\bсаған\b|\bдейін\b|"
    r"մինչև|առավելագույնը|მაქსიმუმ|მდე"
)

BUDGET_UNIT_CUES: list[tuple[str, str]] = [
    (
        r"\bper night\b|\ba night\b|\b/night\b|\bnightly\b|за ночь|\bв сутки\b|\bpro nacht\b|\bpar nuit\b|"
        r"\ba notte\b|\bper notte\b|\bpor noche\b|\bla noche\b|գիշերը|ღამეში",
        "night",
    ),
    (
        r"\bper person\b|\bper head\b|\bpp\b|\bа?с человека\b|на человека|\bpro person\b|\bpar personne\b|"
        r"\ba persona\b|\bper persona\b|\bpor persona\b|մեկ անձի",
        "person",
    ),
    (
        r"\bper month\b|\bmonthly\b|\ba month\b|\b/month\b|в месяц|\bpro monat\b|\bmonatlich\b|\bpar mois\b|"
        r"\bal mese\b|\bal mes\b|\bmensual\w*|ամսական|თვეში|айына",
        "month",
    ),
    (
        r"\btotal\b|\bin total\b|\boverall\b|\bв сумме\b|\bвсего\b|\bитого\b|\binsgesamt\b|\bau total\b|"
        r"\bin totale\b|\ben total\b",
        "total",
    ),
]

EXCLUDE_TRANSPORT = (
    r"\bexcluding (transport|transportation|travel|flights?)\b|\bwithout (transport|flights?)\b|"
    r"\bnot including (transport|flights?)\b|без (учёта )?(дороги|перелёта|транспорта)|"
    r"\bohne (anreise|flug)\b|\bhors transport\b|\bsenza (viaggio|trasporto)\b|\bsin (transporte|vuelos?)\b"
)
