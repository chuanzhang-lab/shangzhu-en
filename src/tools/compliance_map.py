"""行业合规许可证清单 —— **司法辖区数据，不是文案**。

为什么单独拆一个文件、且**不进翻译流水线**：

这些是美国（联邦 / 州 / 市三级）真实存在的许可与登记项。把它们写成
「执照」之类的泛称等于让读者去申请一个不存在的东西 —— 证照名称本身就是
法律名词，改名 = 捏造。故本文件保留**原名**，且不参与 i18n 翻译；
只在展示文案里说明口径（见 en.yaml 的 pt.license_required.warning），
由读者按其所在州/市自行核实。

放在独立模块而不是 pitfall_detector.py 里，是为了让 i18n 护栏
（test_no_hardcoded_cjk_in_converted_modules）不必为一堆证照名开白名单：
本文件是**纯数据**，不会被登记进 CONVERTED_MODULES。

⚠️ 2026-10 由中国大陆口径改为美国口径。改动要点：
- 证照名改为美国真实名称（Business License / Food Service Establishment
  Permit / FDA Food Facility Registration / FMCSA USDOT Number …）。
- **新增英文匹配词表**：旧实现只在描述文本里找中文行业键（"餐饮"），
  英文部署下 `if keyword in text` 永远不成立 —— 合规检测等于静默失效
  （实测：'open a coffee shop' + industry='Food & Beverage' → 命中 0 条）。
  现在中文键与英文关键词都能命中。
- 美国证照以**州 / 市**为主（餐饮许可、美容师执照、房地产经纪牌照都是州级），
  联邦项（FDA / DEA / FMCSA / FinCEN / SEC）只在特定业务下才需要。
  清单给的是「联邦 + 常见州级项」，不是完整清单。
"""

# 行业键（与 industry_templates.yaml 的数据键同源）→ 美国证照 / 登记项
INDUSTRY_COMPLIANCE_MAP = {
    "餐饮": [
        "Business License (city/county)",
        "Food Service Establishment Permit (county health department)",
        "Food Protection Manager Certification (staff)",
        "Certificate of Occupancy",
        "Fire Department Permit (hood / grease extraction)",
        "Seller's Permit — state sales tax",
        "Liquor License (state ABC, if alcohol is served)",
        "EIN — federal tax ID",
    ],
    "食品": [
        "FDA Food Facility Registration (FD&C Act / FSMA)",
        "State Food Processing / Manufacturing License",
        "HACCP plan (required for juice, seafood, meat)",
        "USDA FSIS grant of inspection (if meat/poultry)",
        "FDA food labeling compliance (21 CFR 101)",
        "State cottage-food registration (if home-based)",
    ],
    "医疗": [
        "State medical license (physicians)",
        "State clinic / facility license",
        "CLIA certificate (if running a lab)",
        "DEA registration (if prescribing controlled substances)",
        "HIPAA privacy & security compliance",
        "OSHA bloodborne-pathogens standard",
    ],
    "药品": [
        "State Board of Pharmacy license",
        "DEA registration (controlled substances)",
        "FDA drug establishment registration (if manufacturing/repackaging)",
        "State pharmacy technician registration",
    ],
    "教育": [
        "State private-school license (if operating as a school)",
        "Child-care license (state, if serving minors)",
        "Fingerprinting / background checks for staff",
        "Local business license & zoning approval",
        "FERPA / COPPA compliance (student records)",
    ],
    "金融": [
        "State lending license (if lending or brokering loans)",
        "FinCEN Money Services Business registration (if payments/remittance)",
        "SEC / FINRA registration (if securities activity)",
        "State money-transmitter license",
        "BSA/AML compliance program",
    ],
    "保险": [
        "State insurance producer license",
        "State DOI appointment with each carrier",
        "Surplus-lines license (if placing surplus lines)",
        "FINRA/SEC registration (if variable products)",
    ],
    "证券": [
        "SEC registration (broker-dealer or investment adviser)",
        "FINRA membership + firm registration",
        "Series licenses for individuals (e.g. SIE / 7 / 63 / 65)",
        "State blue-sky registration",
    ],
    "房地产": [
        "State real-estate broker / salesperson license",
        "Firm / brokerage license",
        "Escrow or property-management license (if applicable)",
        "Local business license",
    ],
    "游戏": [
        "No federal operating licence for game publishing",
        "ESRB rating (industry self-regulation)",
        "COPPA compliance (if under-13 users)",
        "State loot-box / sweepstakes rules (vary by state)",
        "Export controls (EAR) if shipping encryption",
    ],
    "直播": [
        "No FCC licence for internet streaming (broadcast spectrum only)",
        "Music performance licences (ASCAP / BMI / SESAC)",
        "FTC endorsement & testimonial rules (16 CFR Part 255)",
        "COPPA (if under-13 audience)",
        "State right-of-publicity / recording-consent rules",
    ],
    "物流": [
        "FMCSA USDOT number",
        "FMCSA operating authority (MC/FF number)",
        "State IRP registration & IFTA fuel-tax licence",
        "CDL for drivers",
        "UCR (Unified Carrier Registration)",
    ],
    "酒店": [
        "State / county lodging licence",
        "Certificate of Occupancy + fire-safety inspection",
        "Health department permit (if food service on site)",
        "Liquor License (if bar service)",
        "Transient-occupancy tax registration",
    ],
    "美容": [
        "State cosmetology / esthetics licence (individual)",
        "Salon establishment licence (state board)",
        "Barber licence (if barbering)",
        "OSHA bloodborne-pathogens standard",
        "Local business licence & zoning",
    ],
    "宠物": [
        "USDA APHIS licence (breeding / boarding / selling, Animal Welfare Act)",
        "State kennel / cattery licence",
        "State veterinary licence (if medical services)",
        "Groomer certification (varies by state)",
        "Local animal-control / zoning permit",
    ],
}

# 英文匹配词（小写比较）：让英文描述也能命中行业。
# 旧实现只匹配中文行业键，英文部署下合规检测静默失效，故必须有这张表。
INDUSTRY_COMPLIANCE_KEYWORDS = {
    "餐饮": ["restaurant", "cafe", "coffee", "diner", "bakery", "bar ",
             "eatery", "food truck", "pizzeria", "noodle", "kitchen"],
    "食品": ["food manufacturing", "food processing", "packaged food",
             "food production", "meal kit", "commercial kitchen"],
    "医疗": ["clinic", "medical", "healthcare", "physician", "doctor",
             "dental", "urgent care", "practice"],
    "药品": ["pharmacy", "drugstore", "pharmaceutical", "prescription", "drug"],
    "教育": ["school", "tutoring", "tutor", "education", "academy",
             "training", "childcare", "preschool", "learning"],
    "金融": ["financ", "lending", "loan", "fintech", "payment",
             "remittance", "credit", "investment"],
    "保险": ["insurance", "underwrit", "broker", "policy"],
    "证券": ["securit", "brokerage", "trading", "investment adviser",
             "wealth management", "stock"],
    "房地产": ["real estate", "realty", "property", "realtor", "landlord"],
    "游戏": ["game", "gaming", "video game", "mobile game", "esports"],
    "直播": ["livestream", "live stream", "streaming", "broadcast",
             "podcast", "influencer", "creator"],
    "物流": ["logistics", "trucking", "freight", "shipping", "delivery",
             "warehouse", "courier"],
    "酒店": ["hotel", "motel", "lodging", "inn", "hostel", "bnb", "resort"],
    "美容": ["salon", "spa", "beauty", "esthetician", "cosmetology",
             "barber", "nail", "skincare"],
    "宠物": ["pet", "veterinary", "vet ", "grooming", "kennel",
             "animal", "dog", "cat"],
}
