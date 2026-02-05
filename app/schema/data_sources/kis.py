from typing import TypedDict


class KisTokenResponse(TypedDict):
    access_token: str
    token_type: str
    expires_in: int


class KisRealtimeQuote(TypedDict, total=False):
    stck_prpr: str
    stck_oprc: str
    stck_hgpr: str
    stck_lwpr: str
    stck_sdpr: str
    acml_vol: str
    acml_tr_pbmn: str
    prdy_vrss: str
    prdy_vrss_sign: str
    prdy_ctrt: str
    stck_mxpr: str
    stck_llam: str
    per: str
    pbr: str
    eps: str
    bps: str
    w52_hgpr: str
    w52_lwpr: str
    w52_hgpr_date: str
    w52_lwpr_date: str
    hts_frgn_ehrt: str
    frgn_ntby_qty: str
    bstp_kor_isnm: str
    rprs_mrkt_kor_name: str
    stck_shrn_iscd: str
    cpfn: str
    lstn_stcn: str


class KisDailyPrice(TypedDict):
    stck_bsop_date: str
    stck_oprc: str
    stck_hgpr: str
    stck_lwpr: str
    stck_clpr: str
    acml_vol: str
    prdy_vrss: str
    prdy_vrss_sign: str
    prdy_ctrt: str
    hts_frgn_ehrt: str
    frgn_ntby_qty: str
    flng_cls_code: str
    acml_prtt_rate: str


class KisMinutePrice(TypedDict):
    stck_bsop_date: str
    stck_cntg_hour: str
    stck_prpr: str
    stck_oprc: str
    stck_hgpr: str
    stck_lwpr: str
    cntg_vol: str
    acml_vol: str
    acml_tr_pbmn: str
