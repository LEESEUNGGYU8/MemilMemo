/* 메밀메모 사이트가 함께 쓰는 움직임: 화면에 들어올 때 떠오르기,
   첫 화면의 장면 돌리기(assets/scene.js). */
(() => {
  "use strict";

  // 화면에 들어올 때 한 번 떠오른다.
  const items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    items.forEach((el) => io.observe(el));
  } else {
    items.forEach((el) => el.classList.add("in"));
  }

  // 첫 화면의 장면. 보이는 동안에만 돌리고, 못 그리면 그림 한 장(noscript 안의 것)을 둔다.
  const host = document.getElementById("scene");
  if (!host) return;
  const spare = host.querySelector("noscript");
  const fallback = () => { if (spare) host.innerHTML = spare.textContent; };
  const Scene = window.MemilScene;
  if (!Scene || !window.MEMIL_SCENE || !("ResizeObserver" in window) || !host.animate) { fallback(); return; }

  const steps = Array.from(document.querySelectorAll("#trail li"));
  const mark = (i) => steps.forEach((li, k) => li.classList.toggle("on", k === i));
  // 주소에 ?still 을 붙이면 멈춘 장면(움직임을 줄인 브라우저가 보는 것)을 그린다.
  const frozen = new URLSearchParams(location.search).has("still");
  let seen = false, shape = null;

  const start = () => {
    shape = Scene.narrow(host);
    try {
      if (frozen) Scene.still(host); else Scene.play(host, mark);
    } catch (e) {
      console.error(e);
      fallback();
    }
  };
  const refresh = () => {
    if (seen && !document.hidden) start();
    else { Scene.stop(); mark(-1); }
  };
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      const now = entries[entries.length - 1].isIntersecting;
      if (now === seen) return;
      seen = now;
      refresh();
    }, { threshold: 0.15 }).observe(host);
  } else {
    seen = true;
    refresh();
  }
  document.addEventListener("visibilitychange", refresh);
  // 폭이 바뀌어 넓은 장면과 좁은 장면이 갈리면 다시 그린다. 그 밖에는 저절로 줄고 는다.
  let timer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (seen && shape !== null && Scene.narrow(host) !== shape) start(); }, 250);
  });
})();
