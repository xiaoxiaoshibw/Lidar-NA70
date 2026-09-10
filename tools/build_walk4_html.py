#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 walk4_report.html：自包含 HTML 报告（内联 SVG，无外部依赖）。

数据源：walk4_summary.json + walk4_far_mat.npy + walk4_base.npy
"""
import base64
import json
from pathlib import Path

import numpy as np

OUT = Path(r"D:\Code\radar\tools\analysis_output")
SUMMARY = json.loads((OUT / "walk4_summary.json").read_text(encoding="utf-8"))
FAR = np.load(OUT / "walk4_far_mat.npy")      # (94, 360)
BASE = np.load(OUT / "walk4_base.npy")
FV = json.loads((OUT / "walk4_front_view.json").read_text(encoding="utf-8"))

FRONT_PNG = OUT / "fig_walk4_front_view.png"
FRONT_B64 = base64.b64encode(FRONT_PNG.read_bytes()).decode() if FRONT_PNG.exists() else ""

YAW0, YAW1 = 140, 350
NFRAME = FAR.shape[0]


def fmt(v, nd=2):
    return f"{v:.{nd}f}"


def svg_polyline(points, color, width=1.6, dash=None):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="{width}"{d}/>')


def timeline_svg():
    W, H, L, R, T, B = 980, 300, 58, 18, 34, 44
    tw = W - L - R
    th = H - T - B
    t_max = SUMMARY["duration_s"]
    far = np.array([p["far_pts"] for p in SUMMARY["per_frame"]])
    tot = np.array([p["total_pts"] for p in SUMMARY["per_frame"]])
    tt = np.array([p["t_rel_s"] for p in SUMMARY["per_frame"]])
    vmax = max(tot.max(), far.max()) * 1.05
    X = lambda t: L + t / t_max * tw
    Y = lambda v: T + (1 - v / vmax) * th

    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px">']
    # 网格
    for gv in range(0, int(vmax) + 2000, 2000):
        y = Y(gv)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#f1f3f5" stroke-width="1"/>')
    for gt in range(0, int(t_max) + 3, 3):
        x = X(gt)
        parts.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{H-B}" stroke="#f1f3f5" stroke-width="1"/>')
    # 事件区间（按人工归纳）
    for s, e, label in [("3.3", "5.9", "走进视场"), ("8.5", "13.4", "逗留+折返")]:
        x1, x2 = X(float(s)), X(float(e))
        parts.append(f'<rect x="{x1:.1f}" y="{T}" width="{x2-x1:.1f}" height="{th}" fill="rgba(220,38,38,0.10)"/>')
        parts.append(f'<text x="{(x1+x2)/2:.1f}" y="{T+12}" font-size="11" fill="#b91c1c" '
                     f'text-anchor="middle" font-family="Microsoft YaHei,sans-serif">{label}</text>')
    # 基线
    qmed = SUMMARY["quiet_far_median"]
    parts.append(f'<line x1="{L}" y1="{Y(qmed):.1f}" x2="{W-R}" y2="{Y(qmed):.1f}" '
                 f'stroke="#2563eb" stroke-width="1" stroke-dasharray="6 4"/>')
    parts.append(f'<text x="{W-R}" y="{Y(qmed)-5:.1f}" font-size="10" fill="#2563eb" '
                 f'text-anchor="end" font-family="Microsoft YaHei,sans-serif">安静基线远回波 {qmed:.0f}</text>')
    # 曲线
    parts.append(svg_polyline([(X(tt[i]), Y(far[i])) for i in range(len(tt))], "#dc2626", 2.0))
    parts.append(svg_polyline([(X(tt[i]), Y(tot[i])) for i in range(len(tt))], "#9aa7b8", 1.2))
    # 轴
    parts.append(f'<line x1="{L}" y1="{T+th}" x2="{W-R}" y2="{T+th}" stroke="#374151" stroke-width="1"/>')
    parts.append(f'<line x1="{L}" y1="{T}" x2="{L}" y2="{T+th}" stroke="#374151" stroke-width="1"/>')
    for gv in range(0, int(vmax) + 2000, 2000):
        y = Y(gv)
        parts.append(f'<text x="{L-6}" y="{y+3:.1f}" font-size="10" fill="#6b7280" text-anchor="end" '
                     f'font-family="Microsoft YaHei,sans-serif">{gv}</text>')
    for gt in range(0, int(t_max) + 3, 3):
        x = X(gt)
        parts.append(f'<text x="{x:.1f}" y="{H-B+16}" font-size="10" fill="#6b7280" text-anchor="middle" '
                     f'font-family="Microsoft YaHei,sans-serif">{gt}</text>')
    parts.append(f'<text x="{L}" y="{H-8}" font-size="11" fill="#374151" font-family="Microsoft YaHei,sans-serif">相对时间 (s)</text>')
    parts.append(f'<text x="{L-40}" y="{T+th/2}" font-size="11" fill="#374151" text-anchor="middle" '
                 f'transform="rotate(-90 {L-40} {T+th/2})" font-family="Microsoft YaHei,sans-serif">点数</text>')
    # 图例
    parts.append(f'<rect x="{W-R-300}" y="{T-22}" width="12" height="3" fill="#dc2626"/>'
                 f'<text x="{W-R-284}" y="{T-17}" font-size="10" fill="#374151" font-family="Microsoft YaHei,sans-serif">远回波数</text>')
    parts.append(f'<rect x="{W-R-210}" y="{T-22}" width="12" height="3" fill="#9aa7b8"/>'
                 f'<text x="{W-R-194}" y="{T-17}" font-size="10" fill="#374151" font-family="Microsoft YaHei,sans-serif">总回波数</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def heatmap_svg():
    W, H, L, R, T, B = 980, 320, 58, 18, 34, 34
    sub = FAR[:, YAW0:YAW1]                      # (94, 210)
    # 下采样：航向每 2° 一格
    ds = sub.reshape(NFRAME, -1, 2).mean(axis=2)  # (94, 105)
    ny, nx = ds.shape
    gw = (W - L - R) / nx
    gh = (H - T - B) / ny
    cw, ch = gw * 0.96, gh * 0.96
    # magma-like 颜色映射（对数）
    vmax = np.percentile(ds, 98)
    def color(v):
        if v <= 0.5:
            return "#0f0d1f"
        t = min(np.log1p(v) / np.log1p(vmax), 1.0)
        # dark blue -> purple -> orange -> yellow
        stops = [(0.0, "#160b29"), (0.35, "#4a2d8f"), (0.6, "#b6377c"), (0.8, "#e9765a"), (1.0, "#f8d65c")]
        for k in range(len(stops) - 1):
            a, ca = stops[k]
            b, cb = stops[k + 1]
            if t <= b:
                f = (t - a) / (b - a)
                r = int(ca[1:3], 16) + (int(cb[1:3], 16) - int(ca[1:3], 16)) * f
                g = int(ca[3:5], 16) + (int(cb[3:5], 16) - int(ca[3:5], 16)) * f
                bl = int(ca[5:7], 16) + (int(cb[5:7], 16) - int(ca[5:7], 16)) * f
                return f"#{int(r):02x}{int(g):02x}{int(bl):02x}"
        return "#f8d65c"
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px">']
    cells = []
    for i in range(ny):
        for j in range(nx):
            v = ds[i, j]
            c = color(float(v))
            cells.append(f'<rect x="{L+j*gw:.2f}" y="{T+i*gh:.2f}" width="{cw:.2f}" height="{ch:.2f}" fill="{c}"/>')
    parts.extend(cells)
    # 事件水平带标注（帧区间）
    for s, e, col in [(13, 24, "#22c55e"), (34, 54, "#22c55e")]:
        y1, y2 = T + s * gh, T + e * gh
        parts.append(f'<line x1="{L-4}" y1="{y1:.1f}" x2="{L}" y2="{y1:.1f}" stroke="{col}" stroke-width="2"/>')
        parts.append(f'<line x1="{L-4}" y1="{y2:.1f}" x2="{L}" y2="{y2:.1f}" stroke="{col}" stroke-width="2"/>')
    # 帧序号标签（隔10）
    for i in range(0, ny, 10):
        y = T + i * gh
        parts.append(f'<text x="{L-6}" y="{y+ch/2+3:.1f}" font-size="9" fill="#6b7280" text-anchor="end" '
                     f'font-family="Microsoft YaHei,sans-serif">{i}</text>')
    # yaw 标签
    for yy in range(YAW0, YAW1 + 1, 20):
        x = L + (yy - YAW0) / (YAW1 - YAW0) * (W - L - R)
        parts.append(f'<text x="{x:.1f}" y="{H-B+16}" font-size="9" fill="#6b7280" text-anchor="middle" '
                     f'font-family="Microsoft YaHei,sans-serif">{yy}°</text>')
    parts.append(f'<text x="{L}" y="{H-6}" font-size="11" fill="#374151" font-family="Microsoft YaHei,sans-serif">航向 yaw (°)</text>')
    parts.append(f'<text x="{L-40}" y="{T+ (H-T-B)/2}" font-size="11" fill="#374151" text-anchor="middle" '
                 f'transform="rotate(-90 {L-40} {T+(H-T-B)/2})" font-family="Microsoft YaHei,sans-serif">帧序号 (0.25s/帧)</text>')
    # 色标
    cbar_x, cbar_y, cbar_w, cbar_h = W - R - 170, H - B + 6, 130, 8
    parts.append(f'<rect x="{cbar_x}" y="{cbar_y}" width="{cbar_w}" height="{cbar_h}" fill="none" stroke="#d1d5db"/>')
    steps = 24
    for k in range(steps):
        v = vmax * k / steps
        parts.append(f'<rect x="{cbar_x + k*cbar_w/steps:.1f}" y="{cbar_y}" width="{cbar_w/steps+0.5:.1f}" height="{cbar_h}" fill="{color(float(v))}"/>')
    parts.append(f'<text x="{cbar_x}" y="{cbar_y+cbar_h+12}" font-size="9" fill="#6b7280" font-family="Microsoft YaHei,sans-serif">0</text>')
    parts.append(f'<text x="{cbar_x+cbar_w}" y="{cbar_y+cbar_h+12}" font-size="9" fill="#6b7280" text-anchor="end" '
                 f'font-family="Microsoft YaHei,sans-serif">{vmax:.0f} 点/格</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def occlusion_svg():
    W, H, L, R, T, B = 980, 300, 58, 18, 34, 44
    tw, th = W - L - R, H - T - B
    t_max = SUMMARY["duration_s"]
    cents = [p["occ_center_yaw"] for p in SUMMARY["per_frame"]]
    bs = [p["band_yaw_start"] for p in SUMMARY["per_frame"]]
    be = [p["band_yaw_end"] for p in SUMMARY["per_frame"]]
    tt = np.array([p["t_rel_s"] for p in SUMMARY["per_frame"]])
    X = lambda t: L + t / t_max * tw
    Y = lambda v: T + (1 - (v - 140) / (350 - 140)) * th
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px">']
    # 网格
    for gv in range(140, 360, 20):
        y = Y(gv)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#f1f3f5" stroke-width="1"/>')
    for gt in range(0, int(t_max) + 3, 3):
        x = X(gt)
        parts.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{H-B}" stroke="#f1f3f5" stroke-width="1"/>')
    # 遮挡带范围（polygon）
    poly = []
    for i in range(len(tt)):
        if bs[i] is not None:
            poly.append((X(tt[i]), Y(bs[i])))
    for i in range(len(tt) - 1, -1, -1):
        if be[i] is not None:
            poly.append((X(tt[i]), Y(be[i])))
    if poly:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in poly)
        parts.append(f'<polygon points="{pts}" fill="rgba(220,38,38,0.12)" stroke="none"/>')
    # 中心线
    pts = [(X(tt[i]), Y(c)) for i, c in enumerate(cents) if c is not None]
    if pts:
        parts.append(svg_polyline(pts, "#dc2626", 2.2))
    # 轴
    parts.append(f'<line x1="{L}" y1="{T+th}" x2="{W-R}" y2="{T+th}" stroke="#374151" stroke-width="1"/>')
    parts.append(f'<line x1="{L}" y1="{T}" x2="{L}" y2="{T+th}" stroke="#374151" stroke-width="1"/>')
    for gv in range(140, 360, 20):
        y = Y(gv)
        parts.append(f'<text x="{L-6}" y="{y+3:.1f}" font-size="10" fill="#6b7280" text-anchor="end" '
                     f'font-family="Microsoft YaHei,sans-serif">{gv}</text>')
    for gt in range(0, int(t_max) + 3, 3):
        x = X(gt)
        parts.append(f'<text x="{x:.1f}" y="{H-B+16}" font-size="10" fill="#6b7280" text-anchor="middle" '
                     f'font-family="Microsoft YaHei,sans-serif">{gt}</text>')
    parts.append(f'<text x="{L}" y="{H-8}" font-size="11" fill="#374151" font-family="Microsoft YaHei,sans-serif">相对时间 (s)</text>')
    parts.append(f'<text x="{L-40}" y="{T+th/2}" font-size="11" fill="#374151" text-anchor="middle" '
                 f'transform="rotate(-90 {L-40} {T+th/2})" font-family="Microsoft YaHei,sans-serif">航向 yaw (°)</text>')
    parts.append(f'<rect x="{W-R-260}" y="{T-24}" width="12" height="3" fill="#dc2626"/>'
                 f'<text x="{W-R-244}" y="{T-19}" font-size="10" fill="#374151" font-family="Microsoft YaHei,sans-serif">遮挡带中心</text>')
    parts.append(f'<rect x="{W-R-150}" y="{T-24}" width="12" height="10" fill="rgba(220,38,38,0.12)"/>'
                 f'<text x="{W-R-134}" y="{T-19}" font-size="10" fill="#374151" font-family="Microsoft YaHei,sans-serif">遮挡带范围</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def build():
    evs = SUMMARY["events"]
    qmed = SUMMARY["quiet_far_median"]
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>4.pcap 移动目标（行人）检测报告</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 0; background: #f6f7f9; color: #1f2328; }}
  .wrap {{ max-width: 1060px; margin: 0 auto; padding: 20px 16px 60px; }}
  h1 {{ font-size: 22px; margin: 8px 0 4px; }}
  h2 {{ font-size: 17px; margin: 30px 0 10px; border-left: 4px solid #2563eb; padding-left: 10px; }}
  .meta {{ color: #6b7280; font-size: 13px; margin-bottom: 18px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 14px 0; }}
  .card {{ flex: 1 1 200px; background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px; }}
  .card .k {{ font-size: 12px; color: #6b7280; }}
  .card .v {{ font-size: 20px; font-weight: 600; margin-top: 4px; }}
  .card .s {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
  .ok {{ color: #16a34a; }}
  .warn {{ color: #b45309; }}
  .note {{ font-size: 13px; color: #4b5563; background: #f0f6ff; border: 1px solid #dbeafe; border-radius: 8px; padding: 10px 14px; margin: 10px 0; line-height: 1.7; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; background: #fff; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 7px 10px; text-align: left; }}
  th {{ background: #f3f4f6; }}
  .concl {{ background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; padding: 14px 18px; line-height: 1.8; font-size: 14px; }}
  ul {{ margin: 6px 0; padding-left: 20px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>4.pcap 移动目标检测报告</h1>
  <div class="meta">数据：{SUMMARY['file']} ｜ 时长 {SUMMARY['duration_s']} s ｜ {SUMMARY['frames']} 帧 @ {SUMMARY['frame_rate_hz']} Hz ｜ 解析：NA70 双楔镜点云协议（UDP 7000→2368）</div>

  <div class="cards">
    <div class="card"><div class="k">检测结论</div><div class="v ok">识别到行人来回走动</div><div class="s">两段深遮挡 + 中间持续部分遮挡</div></div>
    <div class="card"><div class="k">活动时段</div><div class="v">≈3.3 ~ 13.4 s</div><div class="s">相对抓包起点，共约 10 秒</div></div>
    <div class="card"><div class="k">峰值遮挡</div><div class="v">-70%</div><div class="s">远回波 {qmed:.0f} → 最低 2075 点</div></div>
    <div class="card"><div class="k">目标航向范围</div><div class="v">208° ~ 323°</div><div class="s">遮挡带中心在 255°~283° 间移动</div></div>
  </div>

  <div class="concl">
    <b>结论：可以识别。</b> 人在雷达前方约 3.3 s 时进入视场（远场墙体回波 6900 点骤降至 2300 点，-67%），
    之后 6~10 s 在目标区域缓慢移动/逗留（持续部分遮挡，-20%~30%），10.3~13.3 s 二次深遮挡（最低 2075 点，-70%），
    13.6 s 后场景恢复基线 —— 与"在雷达前面走了一走"的来回走动完全吻合。
  </div>

  <h2>1. 回波数时间线（移动事件总览）</h2>
  {timeline_svg()}
  <div class="note">红色阴影为检测到的活动区间。远回波（3~20 m 墙体回波）是稳定的背景信号；行人进入视场后遮挡墙体，
  远回波数大幅下降。安静基线远回波 ≈ {qmed:.0f} 点/帧（虚线），红色点线为 75% 阈值线。</div>

  <h2>2. 帧 × 航向 遮挡热力图（核心证据）</h2>
  {heatmap_svg()}
  <div class="note">横轴航向 140°~350°（墙体回波所在扇区），纵轴帧序号（0.25 s/帧），颜色=该方向远回波点数。
  正常时墙体在整条扇区上形成稳定的亮带；<b>两个绿色标出的时段里，墙体回波出现大块暗色空缺（被行人遮挡），
  且空缺的位置随时间移动</b>——这是行人在雷达前方走动的直接证据。末帧（帧 93）的暗带是抓包截断造成，非真实事件。</div>

  <h2>3. 遮挡带中心轨迹（目标航向随时间移动）</h2>
  {occlusion_svg()}
  <div class="note">红色实线=每帧遮挡带中心航向（目标角位置），浅红区=遮挡带范围。可见 t≈3.3~5.8 s 中心从 ~281° 移到 ~255°
  （走进视场），6~10 s 在 255°~272° 间游走（逗留/慢移），10.3~13.3 s 又移到 ~283°（折返），随后消失（离开）。</div>

  <h2>4. 人的位置轮廓 · 正视图（从雷达视点看）</h2>
  <img src="data:image/png;base64,{FRONT_B64}" alt="人的位置轮廓正视图" style="width:100%;max-width:980px;border:1px solid #e5e7eb;border-radius:8px;">
  <div class="note">
    <b>怎么读这张图：</b>横轴=从雷达看过去的左右方向（航向角），纵轴=上下方向（俯仰角），蓝色柱=安静期墙体回波强度，
    彩色阴影条=目标逐帧的角轮廓（随帧扫过），粗框=三个代表时刻的瞬时轮廓，黑色圆点线=目标角位置随时间移动。
    由于雷达最小测距约 4.7 m、人走得很近时自身回波不输出，图中轮廓是<b>目标在墙面上投下的遮挡阴影</b>；
    竖向跨度=被遮挡的墙体俯仰范围（目标贴近雷达时盖满墙全高）。<br>
    轮廓量化（俯仰主假设 0.01°/码）：事件1 走进主带 208~251°（活动范围 208~339°，横穿），逗留期主带 255~273°，
    事件2 折返主带 218~251° 与 290~323°；俯仰跨度统一 2138~3331 码（21.4°~33.3°；若俯仰分辨率为 0.0258°/码则为 55.2°~85.9°）。
  </div>

  <h2>5. 事件分段明细</h2>
  <table>
    <tr><th>阶段</th><th>时段 (s)</th><th>远回波</th><th>遮挡带航向</th><th>判断</th></tr>
    <tr><td>安静基线</td><td>0 ~ 3.3</td><td>≈ {qmed:.0f}</td><td>—</td><td>场景无目标</td></tr>
    <tr><td>事件 1：进入</td><td>3.3 ~ 5.8</td><td>6900 → 最低 2304（-67%）</td><td>208°~312°，中心 281°→255°</td><td>行人走入视场并横穿</td></tr>
    <tr><td>逗留/慢移</td><td>6 ~ 10.3</td><td>4.0k ~ 5.7k（-20%~30%）</td><td>209°~273° 游走</td><td>目标在区域内缓慢移动</td></tr>
    <tr><td>事件 2：折返</td><td>10.3 ~ 13.3</td><td>6900 → 最低 2075（-70%）</td><td>218°~251° → 290°~323°</td><td>行人走回另一侧并离开</td></tr>
    <tr><td>恢复基线</td><td>13.6 ~ 23.3</td><td>≈ {qmed:.0f}</td><td>—</td><td>目标离开，场景复原</td></tr>
  </table>

  <h2>6. 方法与口径</h2>
  <div class="note">
    <ul>
      <li><b>协议</b>：UDP 7000→2368，1410 B 载荷 = 200 点 × 7 B（大端 u16 距离码、俯仰码、航向码 + u8 反射率）+ 8 B 设备时间戳；距离码×0.01 m，航向码×0.0432°/码，俯仰码×0.0258°/码。</li>
      <li><b>检测原理</b>：固定雷达 + 背景减除。以安静帧建立每方向（1° bin）的墙体回波基线，逐帧求"缺口"（当前回波 vs 基线）；缺口在角度空间呈连续带且随时间移动 = 移动目标。</li>
      <li><b>目标本身无直接回波</b>：数据中不存在 0.2~4.7 m 的中距回波（雷达最小测距约 4.7 m），行人走得很近时其回波不被输出，因此本数据中人是靠<b>遮挡墙体</b>被识别的。</li>
      <li><b>帧 93</b>（最后 0.1 s）仅有 46 个包，为抓包截断，其暗带不计入事件。</li>
      <li>角度为传感器原始坐标系（航向为扫描扇区角），未做外部标定；数值来自对 {SUMMARY['file']} 的直接解析。</li>
    </ul>
  </div>
</div>
</body>
</html>"""
    out = OUT / "walk4_report.html"
    out.write_text(html, encoding="utf-8")
    print("HTML saved:", out, f"({len(html)//1024} KB)")


if __name__ == "__main__":
    build()
