/* 메밀메모 사이트 첫 화면의 움직이는 장면. 한국어, 영어, 일본어 페이지가 함께 쓴다.

   장면 안의 글(메모 내용, 이름표)은 페이지가 window.MEMIL_SCENE 으로 준다.
   MemilScene.play(놓을 곳, 단계가 바뀔 때 부를 함수) 로 돌리고 MemilScene.stop() 으로
   멈춘다. 움직임을 줄이도록 설정한 브라우저에서는 멈춘 장면 하나만 그린다.

   차례: PC 두 대가 같은 메모를 함께 쓰는 모습(고친 글과 새 메모가 내부망으로 건너간다),
   그 뒤로 한 PC 의 바탕화면에서 할 일 체크, 쪽지로 접기, 가장자리에 붙이기, 아이콘으로
   접기, 끌어서 고르기, 폴더에 넣기, 다시 펴기.

   크기와 색, 접는 차례와 시간은 앱이 그리는 값이다(app/ui/note_fold.py, app/config.py):
   머리 줄 32, 쪽지 184x40, 탭 46x40, 아이콘 칸 76x100(그림 48). 접을 때는 아래 절반을
   뒤로 넘기고(0~36%), 남은 위 절반을 옆으로 반 접고(36~68%), 남은 사분면이 쪽지나
   아이콘으로 줄어든다(68~100%). */
(function () {
  "use strict";

  const T = { FOLD: 460, UNFOLD: 400, SLIDE: 200, TAB_HOVER: 140, FADE: 170, A_END: 0.36, B_END: 0.68 };
  // 구간마다 부드럽게 시작해 부드럽게 멈춘다(0.5 - 0.5cos).
  const EASE = "cubic-bezier(0.37, 0, 0.63, 1)";
  const YELLOW = "#FFE066", PINK = "#FFB3BA", BLUE = "#BAE1FF", ORANGE = "#FFD9A6";
  const BARS = {
    "#FFE066": "#FFE27A", "#FFB3BA": "#FFC7CD", "#BAFFC9": "#CEFFD9", "#BAE1FF": "#CEEAFF",
    "#E0BBE4": "#E9CDEC", "#FFD9A6": "#FFE3BC", "#F5F5F5": "#FFFFFF", "#3A3A3A": "#4C4C4C",
  };
  const SLIP = { w: 184, h: 40 };
  const TAB = { w: 46, h: 40, max: 240 };
  const CELL = { w: 76, h: 100 };

  // ── 작은 도구 ──
  const px = (n) => n + "px";
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function css(node, props) { Object.assign(node.style, props); return node; }
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function anim(node, frames, opts) {
    if (!node.animate || reduced()) return Promise.resolve();
    const a = node.animate(frames, opts);
    return a.finished.then(() => a, () => a);
  }

  // ── 색 ──
  function toRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function toHsv([r, g, b]) {
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return [h, max ? (d / max) * 255 : 0, max];
  }
  function fromHsv([h, s, v]) {
    s /= 255;
    const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
      : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return "#" + [r, g, b].map((k) => Math.round(k + m).toString(16).padStart(2, "0")).join("");
  }
  // 앱의 _shade 와 같다: 밝게는 채도를 조금 빼고, 어둡게는 조금 더한다.
  function shade(hex, lighter, amount) {
    const [h, s, v] = toHsv(toRgb(hex));
    return lighter ? fromHsv([h, Math.max(0, s - 20), Math.min(255, v + amount)])
      : fromHsv([h, Math.min(255, s + 18), Math.max(0, v - amount)]);
  }
  function setColor(node, color) {
    const [h, s, v] = toHsv(toRgb(color));
    node.dataset.color = color;
    const st = node.style;
    st.setProperty("--m", color);
    st.setProperty("--m-bar", BARS[color] || shade(color, true, 14));
    // 접어 넣은 모서리에 보이는 종이 뒷면.
    st.setProperty("--m-ear", fromHsv([h, Math.max(0, s - 18), Math.max(0, v - 34)]));
  }

  // ── 그림 ──
  const SPRITE = `
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="mi-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" fill="none"/><path d="M12 7v5l3 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></symbol>
  <symbol id="mi-fold" viewBox="0 0 24 24"><path d="M5 7.5h10.5L20.5 12.5V15.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V9A1.5 1.5 0 0 1 5 7.5z" stroke="currentColor" stroke-width="2" fill="none" stroke-linejoin="round"/><path d="M15.5 7.5v5h5" stroke="currentColor" stroke-width="2" fill="none" stroke-linejoin="round"/></symbol>
  <symbol id="mi-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="19" cy="12" r="1.5" fill="currentColor"/></symbol>
  <symbol id="mi-close" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
  <symbol id="mi-grip" viewBox="0 0 22 22"><path d="M17 13l-4 4M17 8.5 8.5 17M17 4 4 17" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/></symbol>
  <symbol id="mi-check" viewBox="0 0 12 12"><path d="M2.4 6.3l2.4 2.4 4.8-5.2" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/></symbol>
</svg>`;
  function ensureSprite() {
    if (document.getElementById("mi-sprite")) return;
    const holder = el("div");
    holder.id = "mi-sprite";
    holder.innerHTML = SPRITE;
    document.body.insertBefore(holder, document.body.firstChild);
  }
  const use = (name) => `<svg aria-hidden="true"><use href="#mi-${name}"/></svg>`;

  let uid = 0;
  // 메모 색의 작은 종이: 위쪽 띠, 글줄 세 가닥, 접어 넣은 모서리(paint_note_glyph).
  function noteGlyph(color) {
    const id = "ng" + (++uid);
    const x0 = 3.27, y0 = 0.5, x1 = 44.73, y1 = 47.5, w = x1 - x0, h = y1 - y0, r = 4.97, ear = 11.6;
    const path = `M${x0 + r} ${y0}H${x1 - ear}L${x1} ${y0 + ear}V${y1 - r}Q${x1} ${y1} ${x1 - r} ${y1}H${x0 + r}Q${x0} ${y1} ${x0} ${y1 - r}V${y0 + r}Q${x0} ${y0} ${x0 + r} ${y0}Z`;
    const ink = "rgba(0,0,0,.36)", edge = "rgba(0,0,0,.27)";
    const left = x0 + w * 0.2;
    const lines = [[0.42, 0.6], [0.58, 0.6], [0.74, 0.38]].map(([f, k]) =>
      `<path d="M${left} ${y0 + h * f}H${left + w * k}"/>`).join("");
    return `<svg viewBox="0 0 48 48" aria-hidden="true"><defs><clipPath id="${id}"><path d="${path}"/></clipPath></defs>
<path d="${path}" fill="${color}"/>
<g clip-path="url(#${id})"><rect x="${x0}" y="${y0}" width="${w}" height="${h * 0.2}" fill="${shade(color, true, 20)}"/>
<g stroke="${ink}" stroke-width="2.1" stroke-linecap="round">${lines}</g></g>
<path d="M${x1 - ear} ${y0}L${x1} ${y0 + ear}H${x1 - ear}Z" fill="${shade(color, false, 34)}"/>
<path d="M${x1 - ear} ${y0}V${y0 + ear}H${x1}" fill="none" stroke="${edge}"/><path d="${path}" fill="none" stroke="${edge}"/></svg>`;
  }
  // 폴더 색의 폴더(paint_folder_glyph).
  function folderGlyph(color) {
    const line = shade(color, false, 96), back = shade(color, false, 26);
    const L = 0.56, top = 11.26, bottom = 40.13, tabTop = 7.02, r = 4.04, right = 47.44;
    const tabW = (right - L) * 0.42, t2 = top + (bottom - top) * 0.2;
    const backPath = `M${L + r} ${tabTop}H${L + tabW - 3}L${L + tabW + 4} ${top}H${right - r}Q${right} ${top} ${right} ${top + r}V${bottom - r}Q${right} ${bottom} ${right - r} ${bottom}H${L + r}Q${L} ${bottom} ${L} ${bottom - r}V${tabTop + r}Q${L} ${tabTop} ${L + r} ${tabTop}Z`;
    return `<svg viewBox="0 0 48 48" aria-hidden="true"><path d="${backPath}" fill="${back}" stroke="${line}" stroke-width="1.1" stroke-linejoin="round"/>
<rect x="${L}" y="${t2}" width="${right - L}" height="${bottom - t2}" rx="${r}" fill="${color}" stroke="${line}" stroke-width="1.1"/></svg>`;
  }
  // 바탕화면에 함께 놓는 시스템 아이콘. 직접 그린 그림이다.
  function sysGlyph(kind) {
    const id = "sg" + (++uid);
    const screen = `<defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#46D2EE"/><stop offset="1" stop-color="#1B6FD6"/></linearGradient></defs>`;
    if (kind === "bin") {
      return `<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M11.5 12h25l-2.7 26.3a3.2 3.2 0 0 1-3.2 2.9H17.4a3.2 3.2 0 0 1-3.2-2.9z" fill="rgba(225,236,247,.5)" stroke="#EEF3F8" stroke-width="1.4" stroke-linejoin="round"/>
<path d="M9.5 9.6h29v2.8h-29z" fill="#F4F7FA" stroke="#D5DEE8" stroke-width=".8"/>
<g fill="none" stroke="#2F8BE8" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.2 21.5l2.8-4.2 2.9 4.2"/><path d="M30.2 25.2l2 4.6-5.1.6"/><path d="M20.6 30.4l-5.1-.6 2-4.6"/></g></svg>`;
    }
    const monitor = `<rect x="6" y="9" width="36" height="25" rx="2.4" fill="#1C2733"/><rect x="8" y="11" width="32" height="21" rx="1" fill="url(#${id})"/><path d="M20.5 34h7l1.3 5h-9.6z" fill="#A5AFBA"/><rect x="14.5" y="39" width="19" height="2.4" rx="1.2" fill="#CBD3DB"/>`;
    if (kind === "net") {
      return `<svg viewBox="0 0 48 48" aria-hidden="true">${screen}<g transform="translate(4 5) scale(.9)">${monitor}</g>
<circle cx="13" cy="13" r="9.4" fill="#2F8BE8" stroke="#DCEBFA" stroke-width="1.2"/>
<path d="M3.8 13h18.4M13 3.6c-4.4 5-4.4 13.8 0 18.8M13 3.6c4.4 5 4.4 13.8 0 18.8" fill="none" stroke="#DCEBFA" stroke-width="1.1"/></svg>`;
    }
    return `<svg viewBox="0 0 48 48" aria-hidden="true">${screen}${monitor}</svg>`;
  }

  // ── 만들기 ──
  function lineEl(d) {
    const row = el("div", "mm-line");
    if (d.k === "check") {
      const box = el("span", "mm-check");
      box.innerHTML = use("check");
      row.appendChild(box);
    } else if (d.k === "bullet") {
      row.appendChild(el("i", "mm-bullet"));
    }
    const text = el("span", "mm-text", d.t);
    if (d.k === "mark") text.classList.add("mm-mark");
    row.appendChild(text);
    return row;
  }
  // d: {title, color, lines: [{k: check|bullet|mark, t}], x, y, w}
  function memoEl(d) {
    ensureSprite();
    const m = el("div", "mm");
    setColor(m, d.color);
    css(m, { left: px(d.x || 0), top: px(d.y || 0), width: px(d.w || 260) });
    const bar = el("div", "mm-bar");
    // 머리 줄의 왼쪽: 누가 고치고 있는지(앱의 편집 중 표시).
    bar.appendChild(el("span", "mm-lock"));
    for (const name of ["color", "clock", "fold", "more", "close"]) {
      const b = el("span", "mm-btn");
      b.dataset.act = name;
      b.innerHTML = name === "color" ? '<span class="dot"></span>' : use(name);
      bar.appendChild(b);
    }
    const body = el("div", "mm-body");
    body.appendChild(el("p", "mm-title", d.title || ""));
    for (const ln of d.lines || []) body.appendChild(lineEl(ln));
    m.append(bar, body);
    m.insertAdjacentHTML("beforeend", '<svg class="mm-grip" aria-hidden="true"><use href="#mi-grip"/></svg>');
    return m;
  }
  const setLock = (memo, text) => { memo.querySelector(".mm-lock").textContent = text || ""; };
  function slipEl(d) {
    const s = el("div", "slip");
    setColor(s, d.color);
    s.append(el("div", "slip-face"), el("div", "slip-title", d.title || ""));
    return s;
  }
  function tabEl(d) {
    const t = el("div", "tab right");
    setColor(t, d.color);
    t.appendChild(el("span", "tab-title", d.title || ""));
    return t;
  }
  // 마우스를 올리면 제목이 다 보이게 늘어난다.
  function tabExpand(tab, on) {
    if (!on) { tab.style.width = ""; return; }
    const title = tab.querySelector(".tab-title");
    const want = Math.ceil(title.scrollWidth) + 16 + 14;
    tab.style.width = px(Math.max(TAB.w, Math.min(TAB.max, want)));
  }
  // d: {kind: note|folder|pc|bin|net, color, title, count}
  function iconEl(d) {
    const i = el("div", "dicon");
    const g = el("span", "glyph");
    i.append(g, el("span", "label", d.title || ""));
    if (d.kind === "note") g.innerHTML = noteGlyph(d.color);
    else if (d.kind === "folder") g.innerHTML = folderGlyph(d.color);
    else g.innerHTML = sysGlyph(d.kind);
    if (d.count != null) setBadge(i, d.count);
    return i;
  }
  function setBadge(icon, count) {
    const g = icon.querySelector(".glyph");
    let b = g.querySelector(".badge");
    if (!b) { b = el("span", "badge"); g.appendChild(b); }
    b.textContent = String(count);
  }
  // 이름표: 닉네임의 첫 글자를 넣은 동그라미와 이름(참가자 창의 모양).
  function whoEl(label, name, color, cls) {
    const w = el("span", "who" + (cls ? " " + cls : ""));
    const a = el("i", "", Array.from(name)[0] || "");
    a.style.setProperty("--a", color);
    w.append(a, el("b", "", label));
    return w;
  }

  // ── 움직임 ──
  async function slide(node, x, y, ms) {
    const x0 = node.offsetLeft, y0 = node.offsetTop;
    css(node, { left: px(x), top: px(y) });
    if (x0 === x && y0 === y) return;
    await anim(node, [{ transform: `translate(${x0 - x}px, ${y0 - y}px)` }, { transform: "translate(0, 0)" }],
      { duration: ms == null ? T.SLIDE : ms, easing: EASE });
  }
  async function fade(node, show, ms) {
    if (show) node.style.display = "";
    await anim(node, [{ opacity: show ? 0 : 1 }, { opacity: show ? 1 : 0 }],
      { duration: ms == null ? T.FADE : ms, easing: "linear" });
    if (!show) node.style.display = "none";
  }

  // 메모 m 이 접히는(reverse 면 펴지는) 그림을 m 의 자리에서 보여 준다.
  // opts: {face: 다 접힌 모습(쪽지, 아이콘, 탭), anchor: left|right, reverse}
  async function foldAnim(m, opts) {
    const face = opts.face, reverse = !!opts.reverse, right = opts.anchor === "right";
    const kind = face.classList.contains("dicon") ? "icon" : face.classList.contains("tab") ? "tab" : "slip";
    const total = reverse ? T.UNFOLD : T.FOLD;
    const W = m.offsetWidth, H = m.offsetHeight, hh = Math.floor(H / 2), hw = Math.floor(W / 2);
    const color = m.dataset.color;
    m.style.visibility = "hidden";
    if (reduced()) return;
    const stage = css(el("div", "fold-stage"), {
      left: px(m.offsetLeft), top: px(m.offsetTop), width: px(W), height: px(H), zIndex: m.style.zIndex,
    });
    m.parentNode.insertBefore(stage, m.nextSibling);

    const copy = (x, y) => {
      const c = m.cloneNode(true);
      css(c, { left: px(-x), top: px(-y), width: px(W), height: px(H), margin: "0" });
      return c;
    };
    const piece = (x, y, w, h, radius) => {
      const p = css(el("div", "fold-piece"), { left: px(x), top: px(y), width: px(w), height: px(h), borderRadius: radius });
      p.append(copy(x, y), el("div", "fold-shade"));
      return p;
    };
    const run = (d) => ({ duration: d, easing: EASE, fill: "both", direction: reverse ? "reverse" : "normal" });
    const keepX = right ? hw : 0, keepW = right ? W - hw : hw, flapX = right ? 0 : hw, flapW = W - keepW;

    const phases = [
      // 1: 아래 절반을 뒤로 넘긴다.
      async (d) => {
        const top = piece(0, 0, W, hh, "12px 12px 0 0");
        const bottom = piece(0, hh, W, H - hh, "0 0 12px 12px");
        const crease = css(el("div", "fold-crease h"), { left: "0", top: px(hh - 22), width: px(W), height: "22px" });
        bottom.style.transformOrigin = "50% 0";
        stage.append(top, bottom, crease);
        bottom.lastChild.animate([{ opacity: 0 }, { opacity: 0.33 }], run(d));
        crease.animate([{ opacity: 0 }, { opacity: 1, offset: 0.5 }, { opacity: 0 }], run(d));
        await anim(bottom, [{ transform: "perspective(900px) rotateX(0deg)" },
          { transform: "perspective(900px) rotateX(-90deg)" }], run(d));
        top.remove(); bottom.remove(); crease.remove();
      },
      // 2: 남은 위 절반을 좌우로 반 접는다.
      async (d) => {
        const keep = piece(keepX, 0, keepW, hh, right ? "0 12px 0 0" : "12px 0 0 0");
        const flap = piece(flapX, 0, flapW, hh, right ? "12px 0 0 0" : "0 12px 0 0");
        const crease = css(el("div", "fold-crease v"), {
          left: px(right ? keepX : keepW - 22), top: "0", width: "22px", height: px(hh),
          transform: right ? "scaleX(-1)" : "",
        });
        flap.style.transformOrigin = right ? "100% 50%" : "0 50%";
        stage.append(keep, flap, crease);
        flap.lastChild.animate([{ opacity: 0 }, { opacity: 0.33 }], run(d));
        crease.animate([{ opacity: 0 }, { opacity: 1, offset: 0.5 }, { opacity: 0 }], run(d));
        await anim(flap, [{ transform: "perspective(900px) rotateY(0deg)" },
          { transform: `perspective(900px) rotateY(${right ? -90 : 90}deg)` }], run(d));
        keep.remove(); flap.remove(); crease.remove();
      },
      // 3: 남은 사분면이 쪽지(아이콘의 종이, 탭) 크기로 줄어든다. 본문은 줄이지
      //    않고 잘라 내며 지우고, 다 접힌 모습이 제 크기로 나타난다.
      async (d) => {
        const fw = kind === "icon" ? CELL.w : kind === "tab" ? TAB.w : SLIP.w;
        const fx = right ? W - fw : 0;
        const to = kind === "icon" ? { x: fx + 16.77, y: 2, w: 42.46, h: 48, r: "5px" }
          : kind === "tab" ? { x: fx, y: 0, w: TAB.w, h: TAB.h, r: right ? "10px 0 0 10px" : "0 10px 10px 0" }
            : { x: fx, y: 0, w: SLIP.w, h: SLIP.h, r: "10px" };
        const ear = kind === "slip" ? 13 : 0;
        const box = css(el("div", "fold-settle"), { background: color });
        const kept = copy(keepX, 0);
        if (right) css(kept, { left: "auto", right: "0" });
        box.appendChild(kept);
        const shown = css(el("div", "fold-face"), { left: px(fx) });
        const f = face.cloneNode(true);
        f.removeAttribute("style");
        for (const name of ["--m", "--m-bar", "--m-ear"]) {
          f.style.setProperty(name, face.style.getPropertyValue(name));
        }
        f.classList.remove("sel", "hot");
        shown.appendChild(f);
        stage.append(box, shown);
        kept.animate([{ opacity: 1 }, { opacity: 0, offset: 0.55 }, { opacity: 0 }], run(d));
        shown.animate([{ opacity: 0 }, { opacity: 0, offset: 0.3 }, { opacity: 1, offset: 0.9 }, { opacity: 1 }], run(d));
        await anim(box, [
          { left: px(keepX), top: "0px", width: px(keepW), height: px(hh),
            borderRadius: right ? "0 12px 0 0" : "12px 0 0 0",
            clipPath: "polygon(0 0, calc(100% - 0px) 0, 100% 0px, 100% 100%, 0 100%)" },
          { left: px(to.x), top: px(to.y), width: px(to.w), height: px(to.h), borderRadius: to.r,
            clipPath: `polygon(0 0, calc(100% - ${ear}px) 0, 100% ${ear}px, 100% 100%, 0 100%)` },
        ], run(d));
        box.remove(); shown.remove();
      },
    ];
    const parts = [T.A_END, T.B_END - T.A_END, 1 - T.B_END].map((k) => k * total);
    for (const i of reverse ? [2, 1, 0] : [0, 1, 2]) {
      if (!stage.isConnected) break;
      await phases[i](parts[i]);
    }
    stage.remove();
  }
  // 메모를 접어 face(쪽지, 아이콘)로 바꾼다. face 는 메모의 왼쪽 위에 놓인다.
  async function foldTo(m, face) {
    m.parentNode.insertBefore(face, m);
    css(face, { left: px(m.offsetLeft), top: px(m.offsetTop), visibility: "hidden", zIndex: m.style.zIndex });
    await foldAnim(m, { face });
    face.style.visibility = "";
    m.style.display = "none";
    m.style.visibility = "";
    return face;
  }
  // face 를 메모 m 으로 편다. m 은 펼 자리에 놓아 둔다.
  async function unfoldFrom(face, m, opts) {
    m.style.display = "";
    m.style.visibility = "hidden";
    face.style.visibility = "hidden";
    await foldAnim(m, { face, anchor: opts && opts.anchor, reverse: true });
    face.remove();
    m.style.visibility = "";
    return m;
  }

  // ── 흉내 내는 마우스 ──
  function cursor(layer, x, y) {
    const c = el("div", "cur");
    c.innerHTML = '<svg viewBox="0 0 20 28" aria-hidden="true"><path d="M2 2v20.5l5.3-4.7 3.5 8.2 3.1-1.3-3.5-8H18z" fill="#FFFFFF" stroke="#111111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    layer.appendChild(c);
    const at = { x, y };
    const put = () => { c.style.transform = `translate(${at.x - 2}px, ${at.y - 2}px)`; };
    put();
    return {
      get x() { return at.x; },
      get y() { return at.y; },
      // follow(x, y) 는 움직이는 동안 프레임마다 불린다(끌려가는 것을 옮긴다).
      to(nx, ny, ms, follow) {
        const x0 = at.x, y0 = at.y, t0 = performance.now();
        return new Promise((done) => {
          const step = (now) => {
            const u = Math.min(1, (now - t0) / ms);
            const e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
            at.x = x0 + (nx - x0) * e; at.y = y0 + (ny - y0) * e;
            put();
            if (follow) follow(at.x, at.y);
            if (u < 1 && c.isConnected) requestAnimationFrame(step); else done();
          };
          requestAnimationFrame(step);
        });
      },
      down() { c.classList.add("down"); },
      up() { c.classList.remove("down"); },
      async click() { c.classList.add("down"); await sleep(110); c.classList.remove("down"); await sleep(60); },
    };
  }

  // ── 장면 ──
  // 정해 둔 크기(w x h)의 장면을 놓을 곳의 폭에 맞춰 줄이거나 늘린다.
  function fit(host, w, h) {
    host.textContent = "";
    const box = css(el("div", "fit"), { aspectRatio: `${w} / ${h}` });
    const stage = css(el("div", "stage"), { width: px(w), height: px(h) });
    box.appendChild(stage);
    host.appendChild(box);
    stage.setAttribute("aria-hidden", "true");
    stage.inert = true;
    const apply = () => { stage.style.transform = `scale(${box.clientWidth / w})`; };
    apply();
    const watch = new ResizeObserver(apply);
    watch.observe(box);
    return { stage, off: () => watch.disconnect() };
  }
  // 요소 안의 한 점(장면의 좌표). fx, fy 는 0~1 로 준 자리(기본은 한가운데).
  function at(node, layer, fx, fy) {
    const a = node.getBoundingClientRect(), b = layer.getBoundingClientRect();
    const k = b.width / layer.offsetWidth || 1;
    return { x: (a.left - b.left + a.width * (fx == null ? 0.5 : fx)) / k,
      y: (a.top - b.top + a.height * (fy == null ? 0.5 : fy)) / k };
  }
  const hits = (r, n) => r.x < n.offsetLeft + n.offsetWidth && n.offsetLeft < r.x + r.w
    && r.y < n.offsetTop + n.offsetHeight && n.offsetTop < r.y + r.h;
  const narrow = (host) => host.clientWidth < 520;

  // 좁은 화면에서는 PC 두 대를 위아래로 놓고, 새 메모가 건너가는 대목은 뺀다.
  function layout(host) {
    if (narrow(host)) {
      return { narrow: true, W: 400, H: 440, gx: 6, gy: 10, pcW: 400, pcH: 206, strip: 28,
        split: [150, 10], rest: [112, 150], prest: [112, 60], pblank: [116, 150],
        todo: [170, 16], todoW: 220, todoLines: 3, lunch: [154, 240], lunchW: 240,
        tabY: 40, mq: [166, 316, 20, 120], blank: [262, 348] };
    }
    return { narrow: false, W: 660, H: 440, gx: 4, gy: 12, pcW: 326, pcH: 400, strip: 40,
      split: [80, 10], rest: [250, 306], prest: [262, 318], pblank: [40, 300],
      menuAt: [116, 228], blankMe: [42, 356],
      todo: [228, 34], todoW: 228, todoLines: 4, lunch: [404, 250], lunchW: 240,
      tabY: 58, mq: [180, 320, 24, 124], blank: [300, 396] };
  }

  function build(host) {
    const tx = window.MEMIL_SCENE, L = layout(host), f = fit(host, L.W, L.H), stage = f.stage;
    const pc = (cls, x, y, lw, lh) => {
      const panel = css(el("div", "pc " + cls), { left: px(x), top: px(y), width: px(L.pcW), height: px(L.pcH) });
      const layer = css(el("div", "layer"), { width: px(lw), height: px(lh) });
      panel.appendChild(layer);
      stage.appendChild(panel);
      return { panel, layer };
    };
    // 내 PC 의 바탕화면은 장면 전체 크기다. 처음에는 그 왼쪽(좁으면 위쪽)만 보인다.
    const me = pc("me", 0, 0, L.W, L.H);
    const peer = L.narrow ? pc("peer", 0, L.pcH + L.strip, L.pcW, L.pcH) : pc("peer", L.pcW + 8, 0, L.pcW, L.pcH);
    const pos = (c, r) => ({ x: L.gx + c * CELL.w, y: L.gy + r * CELL.h });
    const put = (layer, d, c, r) => {
      const p = pos(c, r), icon = iconEl(d);
      layer.appendChild(css(icon, { left: px(p.x), top: px(p.y) }));
      return icon;
    };
    const s = { tx, L, f, me, peer, layer: me.layer, pos };
    s.pc = put(me.layer, { kind: "pc", title: tx.icons.pc }, 0, 0);
    s.bin = put(me.layer, { kind: "bin", title: tx.icons.bin }, 0, 1);
    s.net = put(me.layer, { kind: "net", title: tx.icons.net }, 0, 2);
    s.folder = put(me.layer, { kind: "folder", color: BLUE, title: tx.icons.folder, count: 2 }, 1, 0);
    s.info = put(me.layer, { kind: "note", color: BLUE, title: tx.icons.info }, 1, 2);
    put(peer.layer, { kind: "pc", title: tx.icons.pc }, 0, 0);
    put(peer.layer, { kind: "bin", title: tx.icons.bin }, 0, 1);

    const lunch = () => memoEl({ title: tx.lunch.title, color: ORANGE, x: L.split[0], y: L.split[1], w: L.lunchW,
      lines: [{ k: "bullet", t: tx.lunch.lines[0] }, { k: "mark", t: tx.lunch.lines[1] }] });
    const todo = () => memoEl({ title: tx.todo.title, color: PINK, x: L.split[0], y: 0, w: L.todoW,
      lines: tx.todo.lines.slice(0, L.todoLines).map((t) => ({ k: "check", t })) });
    s.lunch = lunch(); s.peerLunch = lunch();
    s.todo = todo(); s.peerTodo = todo();
    me.layer.append(s.lunch, s.todo);
    peer.layer.append(s.peerLunch, s.peerTodo);
    // 둘째 줄의 아이콘은 나란히 볼 때 메모에 가려지므로 내 PC 만 남을 때 나타난다.
    s.late = [s.folder, s.info];
    for (const n of [s.todo, s.peerTodo, s.folder, s.info]) n.style.display = "none";

    // 두 PC 를 잇는 줄: 이름표와 '내부망'.
    const strip = css(el("div", "lan" + (L.narrow ? " mid" : "")),
      { left: "0", top: px(L.pcH), width: px(L.W), height: px(L.strip) });
    const line = el("span", "lan-line");
    line.append(el("b", "", tx.lan), el("i", "lan-dot"));
    const mine = whoEl(tx.pcMe, tx.me, YELLOW), theirs = whoEl(tx.pcPeer, tx.peer, ORANGE, "rev");
    if (L.narrow) {
      // 좁은 화면: 이름표는 저마다의 화면 안에 둔다.
      strip.appendChild(line);
      mine.classList.add("on-desk"); theirs.classList.add("on-desk");
      me.panel.appendChild(mine); peer.panel.appendChild(theirs);
    } else {
      strip.append(mine, line, theirs);
    }
    stage.appendChild(strip);
    Object.assign(s, { strip, line, tags: [mine, theirs] });
    return s;
  }
  const addLine = (memo, text) => {
    const row = lineEl({ k: "bullet", t: text }), rows = memo.querySelectorAll(".mm-line");
    memo.querySelector(".mm-body").insertBefore(row, rows[rows.length - 1]);
    return row;
  };
  // 나란히 볼 때 할 일 메모가 놓이는 자리: 점심 메모 바로 아래.
  const todoTop = (memo) => memo.offsetTop + memo.offsetHeight + 8;
  // 내부망으로 건너간다: 보낸 쪽에서 받는 쪽으로 점이 지나간다.
  function pulse(s, from) {
    anim(s.line.firstChild, [{ color: "#5C5544" }, { color: "#D8412D", offset: 0.4 }, { color: "#5C5544" }], { duration: 520 });
    const dot = s.line.lastChild, a = from === "me" ? ["0%", "100%"] : ["100%", "0%"];
    return anim(dot, [{ left: a[0], opacity: 0 }, { opacity: 1, offset: 0.2 }, { opacity: 1, offset: 0.8 }, { left: a[1], opacity: 0 }],
      { duration: 420, easing: "ease-in-out" });
  }
  const arrive = (node) => anim(node, [{ backgroundColor: "rgba(255,255,255,.8)" }, { backgroundColor: "rgba(255,255,255,0)" }],
    { duration: 900, easing: "ease-out" });

  async function loop(host, ctx) {
    const s = build(host), L = s.L, tx = s.tx, layer = s.layer, go = ctx.go;
    ctx.offs.push(s.f.off);
    const cur = cursor(layer, L.rest[0], L.rest[1]);
    const pcur = cursor(s.peer.layer, L.prest[0], L.prest[1]);
    anim(s.f.stage, [{ opacity: 0 }, { opacity: 1 }], { duration: 350 });
    ctx.step(0);
    await go(sleep(460));

    // ── 함께 쓰기 ──
    // 다른 PC 에서 메모를 고친다. 고치는 동안 내 PC 의 같은 메모에 편집 중 표시가 붙고,
    // 고치기를 마치면 글이 건너온다.
    let p = at(s.peerLunch.querySelector(".mm-text"), s.peer.layer, 0.96);
    await go(pcur.to(p.x, p.y, 520));
    await go(pcur.click());
    setLock(s.peerLunch, tx.editing);
    const typed = addLine(s.peerLunch, "").querySelector(".mm-text");
    const letters = document.createTextNode(""), caret = el("i", "mm-caret");
    typed.append(letters, caret);
    await go(sleep(150));
    setLock(s.lunch, tx.editingBy);
    const chars = Array.from(tx.lunch.added);
    for (const ch of chars) {
      letters.data += ch;
      await go(sleep(Math.round(620 / chars.length)));
    }
    await go(sleep(380));
    await go(pcur.to(L.pblank[0], L.pblank[1], 320));
    await go(pcur.click());
    caret.remove();
    setLock(s.peerLunch, "");
    await go(pulse(s, "peer"));
    arrive(addLine(s.lunch, tx.lunch.added));
    setLock(s.lunch, "");
    await go(sleep(540));

    if (!L.narrow) {
      // 내 PC 에서 새 메모를 만든다(바탕화면 우클릭 메뉴). 다 쓰면 다른 PC 에도 생긴다.
      await go(cur.to(L.menuAt[0], L.menuAt[1], 420));
      await go(cur.click());
      const menu = css(el("div", "sc-menu"), { left: px(L.menuAt[0] + 3), top: px(L.menuAt[1] + 3) });
      menu.append(el("div", "", tx.newNote), el("div", "", tx.newFolder));
      layer.appendChild(menu);
      anim(menu, [{ opacity: 0 }, { opacity: 1 }], { duration: 110 });
      p = at(menu.firstChild, layer, 0.3);
      await go(cur.to(p.x, p.y, 240));
      menu.firstChild.classList.add("hot");
      await go(cur.click());
      menu.remove();
      const y = todoTop(s.lunch), parts = Array.from(s.todo.querySelector(".mm-body").children);
      for (const n of parts) n.style.display = "none";
      s.todo.style.top = px(y);
      setLock(s.todo, tx.editing);
      await go(fade(s.todo, true));
      for (const n of parts) { n.style.display = ""; await go(sleep(110)); }
      await go(sleep(260));
      await go(cur.to(L.blankMe[0], L.blankMe[1], 300));
      await go(cur.click());
      setLock(s.todo, "");
      await go(pulse(s, "me"));
      s.peerTodo.style.top = px(y);
      await go(fade(s.peerTodo, true));
      await go(sleep(520));
    }

    // 다른 PC 는 물러나고 내 PC 의 바탕화면이 장면을 채운다.
    {
      const me = s.me.panel, peer = s.peer.panel, ms = 560;
      const w0 = me.offsetWidth, h0 = me.offsetHeight;
      css(me, { width: px(L.W), height: px(L.H), zIndex: "2" });
      anim(me, [{ width: px(w0), height: px(h0) }, { width: px(L.W), height: px(L.H) }], { duration: ms, easing: EASE });
      anim(peer, [{ transform: "none" }, { transform: L.narrow ? `translateY(${L.pcH + 40}px)` : `translateX(${L.pcW + 20}px)` }],
        { duration: ms, easing: EASE, fill: "forwards" });
      for (const n of [s.strip].concat(L.narrow ? s.tags : [])) anim(n, [{ opacity: 1 }, { opacity: 0 }], { duration: 160, fill: "forwards" });
      const moves = [slide(s.lunch, L.lunch[0], L.lunch[1], ms)];
      if (L.narrow) css(s.todo, { left: px(L.todo[0]), top: px(L.todo[1]) });
      else moves.push(slide(s.todo, L.todo[0], L.todo[1], ms));
      await go(Promise.all(moves));
      peer.remove(); s.strip.remove();
      for (const n of s.tags) n.remove();
      for (const n of s.late.concat(L.narrow ? [s.todo] : [])) fade(n, true, 260);
      await go(sleep(L.narrow ? 420 : 200));
    }

    // 1. 할 일 체크
    ctx.step(1);
    const box = s.todo.querySelector(".mm-check");
    p = at(box, layer);
    await go(cur.to(p.x, p.y, 640));
    await go(cur.click());
    box.closest(".mm-line").classList.add("done");
    await go(sleep(500));

    // 2. 쪽지로 접기
    ctx.step(2);
    const foldBtn = s.todo.querySelector('[data-act="fold"]');
    p = at(foldBtn, layer);
    await go(cur.to(p.x, p.y, 540));
    foldBtn.classList.add("hot");
    await go(cur.click());
    const slip = slipEl({ title: tx.todo.title, color: PINK });
    await go(foldTo(s.todo, slip));
    foldBtn.classList.remove("hot");
    await go(sleep(440));

    // 3. 가장자리에 붙이기: 쪽지를 오른쪽 끝으로 끌면 탭이 된다.
    ctx.step(3);
    await go(cur.to(slip.offsetLeft + 64, slip.offsetTop + 22, 400));
    cur.down();
    const ox = slip.offsetLeft - cur.x, oy = slip.offsetTop - cur.y;
    await go(cur.to(L.W - 34, L.tabY + 22, 740, (x, y) => css(slip, { left: px(x + ox), top: px(y + oy) })));
    cur.up();
    await go(slide(slip, L.W - TAB.w, L.tabY, T.SLIDE));
    const tab = css(tabEl({ title: tx.todo.title, color: PINK }), { top: px(L.tabY) });
    layer.insertBefore(tab, slip);
    slip.remove();
    // 마우스가 탭 위에 있는 동안에는 제목이 다 보인다.
    await go(sleep(240));
    tabExpand(tab, true);
    await go(sleep(760));
    await go(cur.to(L.W - 190, L.tabY + 120, 380));
    tabExpand(tab, false);
    await go(sleep(220));

    // 4. 아이콘으로 접기: 바탕화면 아이콘의 빈 칸으로 간다.
    ctx.step(4);
    const foldBtn2 = s.lunch.querySelector('[data-act="fold"]');
    p = at(foldBtn2, layer);
    await go(cur.to(p.x, p.y, 620));
    foldBtn2.classList.add("hot");
    await go(cur.click());
    const icon = iconEl({ kind: "note", color: ORANGE, title: tx.lunch.title });
    await go(foldTo(s.lunch, icon));
    const cell = s.pos(1, 1);
    await go(slide(icon, cell.x, cell.y, T.SLIDE));
    await go(sleep(440));

    // 5. 끌어서 고르기: 빈 곳을 끌면 진짜 아이콘과 함께 골라진다.
    ctx.step(5);
    const [mx, my, ex, ey] = L.mq;
    await go(cur.to(mx, my, 540));
    cur.down();
    const mq = el("div", "marquee");
    layer.appendChild(mq);
    const pool = [s.bin, s.net, s.info, icon];
    await go(cur.to(ex, ey, 860, (x, y) => {
      const r = { x: Math.min(mx, x), y: Math.min(my, y), w: Math.abs(x - mx), h: Math.abs(y - my) };
      css(mq, { left: px(r.x), top: px(r.y), width: px(r.w), height: px(r.h) });
      for (const n of pool) n.classList.toggle("sel", hits(r, n));
    }));
    cur.up();
    mq.remove();
    await go(sleep(580));

    // 6. 폴더에 넣기
    ctx.step(6);
    await go(cur.to(L.blank[0], L.blank[1], 380));
    await go(cur.click());
    for (const n of pool) n.classList.remove("sel");
    await go(sleep(150));
    p = at(icon.querySelector(".glyph"), layer);
    await go(cur.to(p.x, p.y, 420));
    cur.down();
    icon.classList.add("sel");
    icon.style.zIndex = "8";
    const ix = icon.offsetLeft - cur.x, iy = icon.offsetTop - cur.y;
    const fc = at(s.folder.querySelector(".glyph"), layer);
    await go(cur.to(fc.x + 2, fc.y + 8, 600, (x, y) => {
      css(icon, { left: px(x + ix), top: px(y + iy) });
      s.folder.classList.toggle("drop", Math.abs(y - fc.y) < 46);
    }));
    cur.up();
    await go(anim(icon, [{ opacity: 1, transform: "scale(1)" }, { opacity: 0, transform: "scale(0.6)" }],
      { duration: T.FADE, fill: "forwards" }));
    icon.remove();
    s.folder.classList.remove("drop");
    setBadge(s.folder, 3);
    anim(s.folder.querySelector(".badge"), [{ transform: "scale(1.5)" }, { transform: "scale(1)" }],
      { duration: 260, easing: EASE });
    await go(sleep(560));

    // 7. 다시 펴기: 탭을 누르면 그 자리에서 펴진다.
    ctx.step(7);
    await go(cur.to(L.W - 22, L.tabY + 22, 620));
    tabExpand(tab, true);
    await go(sleep(400));
    await go(cur.click());
    tabExpand(tab, false);
    tab.style.transition = "none";
    css(s.todo, { left: px(L.W - L.todoW), top: px(L.tabY) });
    await go(unfoldFrom(tab, s.todo, { anchor: "right" }));
    await go(cur.to(L.W - L.todoW - 60, L.tabY + 150, 500));
    await go(sleep(900));
    await go(anim(s.f.stage, [{ opacity: 1 }, { opacity: 0 }], { duration: 320, fill: "forwards" }));
    s.f.off();
  }

  // 멈춘 장면: PC 두 대에 같은 메모가 있고, 한쪽에서 고치는 중이다.
  function still(host) {
    const s = build(host), tx = s.tx;
    addLine(s.lunch, tx.lunch.added);
    addLine(s.peerLunch, tx.lunch.added).querySelector(".mm-text").appendChild(el("i", "mm-caret"));
    setLock(s.lunch, tx.editingBy);
    setLock(s.peerLunch, tx.editing);
    if (!s.L.narrow) {
      for (const [memo, above] of [[s.todo, s.lunch], [s.peerTodo, s.peerLunch]]) {
        memo.style.display = "";
        memo.style.top = px(todoTop(above));
      }
    }
    return [s.f.off];
  }

  // ── 돌리기 ──
  const STOP = { stop: true };
  let turn = 0, offs = [];

  function stop() {
    turn++;
    for (const off of offs) { try { off(); } catch (e) { /* 이미 끊겼다 */ } }
    offs = [];
  }
  function showStill(host) {
    stop();
    offs = still(host) || [];
  }
  function play(host, onStep) {
    stop();
    if (reduced()) { showStill(host); return; }
    const mine = turn, alive = () => mine === turn;
    const ctx = {
      offs,
      step: (i) => { if (alive() && onStep) onStep(i); },
      // 멈췄으면 기다리던 자리에서 그만둔다.
      go: async (promise) => {
        const value = await promise;
        if (!alive()) throw STOP;
        return value;
      },
    };
    (async () => {
      try {
        while (alive()) await loop(host, ctx);
      } catch (e) {
        if (e !== STOP) console.error(e);
      }
    })();
  }

  window.MemilScene = { play, still: showStill, stop, narrow };
})();
