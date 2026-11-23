#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
물길트윈 - 승인된 시나리오 데이터 생성기 v3 (CLAUDE.md §2-1 적용)

v3 변경점 (2026-11-22, 11_22_전달.zip 최종 자료 반영):
  - 수요량 개념 제거(2026-11-22 김선명 결정). 대신 '평시 공급 기준선'을 쓴다.
            facilities.geojson의 실제 supply_ton: 유강 42,378 + 제2수원지 49,125
            = 91,503 톤/일. 출력은 '수요 충족률'이 아니라 '평시 공급 유지율'.
  - 대체수원: 영천댐 -> **안계댐(dam_01)** 으로 교정.
            api_code_mapping.csv에 "안계댐 = 중명취수장 권역(유강·제2수원지·공단)
            핵심 대체수원"으로 명시됨. 영천댐(dam_02)은 양덕정수장 계통이라 무관.
  - 경로: emergency_pipelines.geojson의 실제 관로 사용
            평시 pipe_04/05 (중명->유강/제2수원지), 비상 pipe_01/02 (안계댐->동일 정수장),
            제안 pipe_07 (양덕->유강 광역연계) = 보라색 '대체 추천 경로'
  - EC 가상센서에 rec_01(중명취수장 직하류 추천지점) 추가 -> 센서 설치 효과 시연용
  - 시설 ID를 facilities.geojson 표준(int_01/wtp_01/dam_01)으로 통일

EC 등급 기준 (2026-11-22 제품담당자 김선명 확정):
  - EC <= 700        : 정상        - 파랑(#2F80ED), 조치 없음
  - 700 < EC < 3000  : 주의 및 경계 - 주황(#F2994A), 경고 신호 발송
  - EC >= 3000       : 심각        - 빨강(#EB5757), 무조건 대체 수원 전환
  - 대체 추천 경로    : 보라(#9B51E0)  ※ 등급이 아니라 경로 종류 색상

여전히 임의값인 것 (실자료 받으면 교체):
  - EC 수치 자체 (가상 센서 시계열)
  - 안계댐 비상도수 이송 가능 용량 (관로 용량 자료 미수령)
  - EC -> 담수 가용비율 보간식 (공유 이미지의 산식이 잘려 전체를 못 봄)
"""
import json
import csv
import math
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = "output"

SCENARIO_ID = "SCN-2026-11-SALTWATER-EVENT-02"
SCENARIO_NAME = "형산강 하구 염수침입 시연 시나리오 (중명취수장 계통)"
PROVIDER = f"scenario:{SCENARIO_ID}"

# --- 등급 기준 (김선명 확정) ---
EC_NORMAL_MAX = 700.0
EC_SEVERE_MIN = 3000.0
GRADE_COLORS = {"정상": "#2F80ED", "주의_경계": "#F2994A", "심각": "#EB5757"}
ROUTE_COLOR_ALTERNATE = "#9B51E0"

# --- 실제 자료에서 가져온 값 (facilities.geojson / api_code_mapping.csv) ---
INTAKE = {"id": "int_01", "name": "중명취수장", "source_water": "형산강", "lon": 129.316562, "lat": 35.996562}
SERVED_WTPS = [
    {"id": "wtp_01", "name": "유강정수장", "supply_ton": 42378, "normal_route": "pipe_04", "alt_route": "pipe_01"},
    {"id": "wtp_02", "name": "제2수원지정수장", "supply_ton": 49125, "normal_route": "pipe_05", "alt_route": "pipe_02"},
]
BASELINE_SUPPLY_TON_D = sum(w["supply_ton"] for w in SERVED_WTPS)  # 91,503 (실제값)
ALT_SOURCE = {"id": "dam_01", "name": "안계댐", "routes": ["pipe_01", "pipe_02"]}
PROPOSED_ROUTE = {"id": "pipe_07", "name": "양덕-유강 광역연계 제안관로", "status": "proposed",
                  "color": ROUTE_COLOR_ALTERNATE}

# --- 임의 가정값 (실자료 수령 시 교체) ---
ALT_CAP_TON_D = 60000.0  # 안계댐 비상도수 이송 가능량: 관로 용량 자료 없어 시나리오 가정

STATIONS = [
    {"station_id": "sensor_01", "name": "형산강하구_관측소", "lon": 129.3665, "lat": 36.0135,
     "influence": 1.00, "kind": "existing"},
    {"station_id": "sensor_02", "name": "연일대교_관측소", "lon": 129.3515, "lat": 36.0055,
     "influence": 0.55, "kind": "existing"},
    {"station_id": "rec_01", "name": "중명취수장_직하류_추천지점", "lon": 129.3350, "lat": 36.0037,
     "influence": 0.40, "kind": "recommended"},
    {"station_id": "sensor_03", "name": "강동대교_관측소", "lon": 129.3085, "lat": 35.9915,
     "influence": 0.20, "kind": "existing"},
]
EC_REF_STATION = "rec_01"  # 중명취수장 직하류 = 취수 판단 기준점

BASIS = (
    "EC 등급 경계(정상 <=700 / 주의·경계 700~3000 / 심각 >=3000 uS/cm)는 2026-11-22 "
    "제품담당자(김선명) 확정값. 평시 공급 기준선은 facilities.geojson의 실제 supply_ton "
    f"(유강 42,378 + 제2수원지 49,125 = {BASELINE_SUPPLY_TON_D:,} 톤/일). 대체수원·관로는 "
    "api_code_mapping.csv와 emergency_pipelines.geojson의 실제 연결관계(안계댐 -> "
    "유강/제2수원지 비상도수관로). EC 수치와 안계댐 이송 용량만 시연용 임의값."
)
METHOD = (
    "하구로부터의 거리에 따른 감쇠계수를 적용한 조위 연동(반일주조 12.4h) 합성파형 + "
    "1회 고조위 염수침입 이벤트로 4개 지점 EC 생성. 취수 판단은 중명취수장 직하류 "
    "추천지점(rec_01) EC를 기준으로 하고, 일중 최고값으로 보수적 등급 판정. "
    "심각(>=3000)이면 본류 취수를 0으로 강제 전환하고 전량 안계댐 비상도수로 충당."
)

START = datetime(2026, 11, 24, 0, 0, tzinfo=KST)
HOURS = 24 * 7
DAYS = 7


def ec_base_curve(hour_index: int, influence: float) -> float:
    t = hour_index
    tidal = 60.0 * math.sin(2 * math.pi * t / 12.4)
    baseline = 300.0 + 150.0 * influence
    event = (9500.0 * influence) * math.exp(-((t - 72.0) ** 2) / (2 * 14.0 ** 2))
    return max(50.0, baseline + tidal + event)


def ec_grade(value: float) -> str:
    if value <= EC_NORMAL_MAX:
        return "정상"
    if value < EC_SEVERE_MIN:
        return "주의_경계"
    return "심각"


def grade_action(grade: str) -> str:
    return {"정상": "none", "주의_경계": "warn", "심각": "force_switch"}[grade]


def freshwater_fraction(ec: float) -> float:
    if ec <= EC_NORMAL_MAX:
        return 1.0
    if ec < EC_SEVERE_MIN:
        return 1.0 - 0.70 * (ec - EC_NORMAL_MAX) / (EC_SEVERE_MIN - EC_NORMAL_MAX)
    return 0.0  # 심각 = 무조건 대체


def main():
    # ---------- 1) EC 가상 센서 시계열 ----------
    ec_rows = []
    ref_hourly = []
    for h in range(HOURS):
        ts = START + timedelta(hours=h)
        for st in STATIONS:
            ec = round(ec_base_curve(h, st["influence"]), 1)
            grade = ec_grade(ec)
            if st["station_id"] == EC_REF_STATION:
                ref_hourly.append((ts, ec))
            ec_rows.append({
                "station_id": st["station_id"],
                "station_name": st["name"],
                "station_kind": st["kind"],
                "observed_at": ts.isoformat(),
                "variable": "EC",
                "value": ec,
                "unit": "uS/cm",
                "grade": grade,
                "grade_color": GRADE_COLORS[grade],
                "action": grade_action(grade),
                "data_type": "scenario",
                "scenario_id": SCENARIO_ID,
                "provider": PROVIDER,
                "quality_flag": "scenario_generated",
            })

    with open(f"{OUT}/ec_virtual_sensor_timeseries.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(ec_rows[0].keys()))
        w.writeheader()
        w.writerows(ec_rows)

    # ---------- 2) 가용량 / 공급량 (일 단위) ----------
    daily_rows = []
    route_rows = []
    for d in range(DAYS):
        day_start = START + timedelta(days=d)
        vals = [v for ts, v in ref_hourly if day_start <= ts < day_start + timedelta(days=1)]
        ec_max, ec_avg = max(vals), round(sum(vals) / len(vals), 1)
        grade = ec_grade(ec_max)
        action = grade_action(grade)
        frac = freshwater_fraction(ec_max)
        forced = grade == "심각"

        available = round(BASELINE_SUPPLY_TON_D * frac, 1)          # 담수 가용량 (평시 기준선 환산)
        mainstem = 0.0 if forced else round(min(available, BASELINE_SUPPLY_TON_D), 1)
        shortfall = round(BASELINE_SUPPLY_TON_D - mainstem, 1)
        alt_used = round(min(shortfall, ALT_CAP_TON_D), 1)
        total = round(mainstem + alt_used, 1)
        unmet = round(max(0.0, BASELINE_SUPPLY_TON_D - total), 1)
        pct = round(100.0 * total / BASELINE_SUPPLY_TON_D, 1)

        daily_rows.append({
            "date": day_start.date().isoformat(),
            "intake_id": INTAKE["id"], "intake_name": INTAKE["name"],
            "ec_ref_station": EC_REF_STATION,
            "ec_day_max_uScm": ec_max, "ec_day_avg_uScm": ec_avg,
            "grade": grade, "grade_color": GRADE_COLORS[grade],
            "action": action, "forced_switch": forced,
            "freshwater_fraction": round(frac, 3),
            "baseline_supply_ton_d": BASELINE_SUPPLY_TON_D,
            "mainstem_supply_ton_d": mainstem,
            "shortfall_ton_d": shortfall,
            "alt_source_id": ALT_SOURCE["id"], "alt_source_name": ALT_SOURCE["name"],
            "alt_cap_ton_d": ALT_CAP_TON_D,
            "alt_supply_used_ton_d": alt_used,
            "total_supply_ton_d": total,
            "supply_gap_ton_d": unmet,
            "supply_maintain_pct": pct,
            "data_type": "scenario", "scenario_id": SCENARIO_ID,
            "provider": PROVIDER, "label": "시뮬레이션 결과",
        })

        # 정수장별 + 경로별 상태 (지도 색칠용)
        for wtp in SERVED_WTPS:
            share = wtp["supply_ton"] / BASELINE_SUPPLY_TON_D
            route_rows.append({
                "date": day_start.date().isoformat(),
                "route_id": wtp["normal_route"], "route_status": "normal",
                "from_id": INTAKE["id"], "to_id": wtp["id"],
                "flow_ton_d": round(mainstem * share, 1),
                "grade": grade, "color": GRADE_COLORS[grade],
                "data_type": "scenario", "scenario_id": SCENARIO_ID,
            })
            route_rows.append({
                "date": day_start.date().isoformat(),
                "route_id": wtp["alt_route"], "route_status": "existing_emergency",
                "from_id": ALT_SOURCE["id"], "to_id": wtp["id"],
                "flow_ton_d": round(alt_used * share, 1),
                "grade": "대체가동" if alt_used > 0 else "대기",
                "color": ROUTE_COLOR_ALTERNATE if alt_used > 0 else "#9E9E9E",
                "data_type": "scenario", "scenario_id": SCENARIO_ID,
            })
        route_rows.append({
            "date": day_start.date().isoformat(),
            "route_id": PROPOSED_ROUTE["id"], "route_status": "proposed",
            "from_id": "wtp_03", "to_id": "wtp_01",
            "flow_ton_d": 0.0,
            "grade": "추천" if unmet > 0 else "대기",
            "color": ROUTE_COLOR_ALTERNATE,
            "data_type": "scenario", "scenario_id": SCENARIO_ID,
        })

    with open(f"{OUT}/dss_capacity_supply_demand_daily.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(daily_rows[0].keys()))
        w.writeheader()
        w.writerows(daily_rows)

    with open(f"{OUT}/dss_route_state_daily.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(route_rows[0].keys()))
        w.writeheader()
        w.writerows(route_rows)

    # ---------- 3) 파라미터 JSON ----------
    params = {
        "scenario_id": SCENARIO_ID, "scenario_name": SCENARIO_NAME,
        "data_type": "scenario", "provider": PROVIDER,
        "생성_근거": BASIS, "생성_방법": METHOD,
        "생성_시각_KST": datetime.now(KST).isoformat(),
        "승인": "제품담당자(김선명) 2026-11-22 기준확정 및 생성 지시 (CLAUDE.md §2-1)",
        "EC_grade_rule": {
            "확정자": "김선명", "확정일": "2026-11-22", "단위": "uS/cm",
            "정상": {"max_inclusive": EC_NORMAL_MAX, "color": GRADE_COLORS["정상"], "action": "none"},
            "주의_경계": {"min_exclusive": EC_NORMAL_MAX, "max_exclusive": EC_SEVERE_MIN,
                         "color": GRADE_COLORS["주의_경계"], "action": "warn", "설명": "경고 신호 발송"},
            "심각": {"min_inclusive": EC_SEVERE_MIN, "color": GRADE_COLORS["심각"],
                    "action": "force_switch", "설명": "무조건 대체 수원 전환"},
            "대체_추천_경로_색상": ROUTE_COLOR_ALTERNATE,
        },
        "실제값_출처": {
            "baseline_supply_ton_d": {"value": BASELINE_SUPPLY_TON_D, "source": "facilities.geojson supply_ton (wtp_01+wtp_02)"},
            "intake": INTAKE, "served_wtps": SERVED_WTPS,
            "alt_source": {**ALT_SOURCE, "source": "api_code_mapping.csv - 중명취수장 권역 핵심 대체수원"},
            "proposed_route": PROPOSED_ROUTE,
        },
        "임의_가정값": {
            "alt_cap_ton_d": {"value": ALT_CAP_TON_D,
                              "reason": "안계댐 비상도수관로 이송 가능 용량 자료 미수령 - 확인 필요"},
            "ec_values": "가상 센서 시계열 전체",
            "freshwater_fraction_piecewise": [
                {"ec_lte": EC_NORMAL_MAX, "fraction": 1.0},
                {"ec_range": [EC_NORMAL_MAX, EC_SEVERE_MIN], "fraction_range": [1.0, 0.30], "interp": "linear"},
                {"ec_gte": EC_SEVERE_MIN, "fraction": 0.0, "note": "심각 = 본류 취수 중단"},
            ],
        },
        "stations": STATIONS,
        "event_window_KST": {
            "start": (START + timedelta(hours=48)).isoformat(),
            "peak": (START + timedelta(hours=72)).isoformat(),
            "end": (START + timedelta(hours=96)).isoformat(),
        },
        "time_range_KST": {"start": START.isoformat(), "hours": HOURS},
        "주의사항": [
            "EC 수치와 안계댐 이송용량은 실측이 아닌 임의 시나리오 값이다.",
            "provider='scenario:*' 로 분리 저장하고 기존 실측 행을 덮어쓰지 않는다.",
            "이 시나리오 기반 화면에는 반드시 '시뮬레이션 결과' 표시를 포함할 것.",
        ],
        "확인필요": [
            "dam_01 ID 충돌: facilities.geojson=안계댐 / existing_stations.geojson=영천댐_대체수원. 통일 필요.",
            "안계댐·영천댐 비상도수관로 이송 가능 용량(톤/일) 실제값.",
            "중명취수장 자체 취수 정격용량(현재는 정수장 생산량 합계로 환산 중).",
        ],
    }
    with open(f"{OUT}/scenario_parameters.json", "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)

    counts = {}
    for r in ec_rows:
        counts[r["grade"]] = counts.get(r["grade"], 0) + 1
    print(f"평시 공급 기준선(실제): {BASELINE_SUPPLY_TON_D:,} 톤/일 | 대체수원: {ALT_SOURCE['name']} (상한 가정 {ALT_CAP_TON_D:,.0f})")
    print("EC rows:", len(ec_rows), counts, "| route rows:", len(route_rows))
    for r in daily_rows:
        print(f"{r['date']} EC최고 {r['ec_day_max_uScm']:>7} {r['grade']:<6} {r['action']:<12} "
              f"본류 {r['mainstem_supply_ton_d']:>9,.0f} 대체 {r['alt_supply_used_ton_d']:>8,.0f} "
              f"공급부족 {r['supply_gap_ton_d']:>8,.0f} 유지율 {r['supply_maintain_pct']}%")


if __name__ == "__main__":
    main()
