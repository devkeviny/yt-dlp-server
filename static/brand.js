// Lulu Downloader — logo de gato (SVG inline, paleta MEDIA_CORE_V1)
// Uso: <span id="logo"></span> + renderLogo('horizontal'|'vertical', 'md'|'sm'|'lg', el)
(function(){
  const CAT = `
<svg class="lu-cat" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="Lulu Downloader">
  <defs>
    <linearGradient id="luGrad" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
      <stop stop-color="#00f0ff"/><stop offset="1" stop-color="#7000ff"/>
    </linearGradient>
  </defs>
  <!-- orelhas -->
  <path d="M14 22 L10 6 L26 16 Z" fill="url(#luGrad)"/>
  <path d="M50 22 L54 6 L38 16 Z" fill="url(#luGrad)"/>
  <!-- cabeca -->
  <path d="M32 14 C18 14 12 24 12 34 C12 46 21 54 32 54 C43 54 52 46 52 34 C52 24 46 14 32 14 Z" fill="#0e0e0e" stroke="url(#luGrad)" stroke-width="2.5"/>
  <!-- olhos -->
  <ellipse cx="24" cy="33" rx="3.4" ry="4.6" fill="#00f0ff"/>
  <ellipse cx="40" cy="33" rx="3.4" ry="4.6" fill="#7000ff"/>
  <circle cx="24" cy="33" r="1.5" fill="#0e0e0e"/>
  <circle cx="40" cy="33" r="1.5" fill="#0e0e0e"/>
  <!-- focinho -->
  <path d="M29 41 L35 41 L32 45 Z" fill="url(#luGrad)"/>
  <path d="M32 45 L32 48" stroke="url(#luGrad)" stroke-width="1.6" stroke-linecap="round"/>
  <!-- bigodes -->
  <path d="M30 45 L18 43 M30 47 L18 49 M34 45 L46 43 M34 47 L46 49" stroke="#b9cacb" stroke-width="1.2" stroke-linecap="round"/>
</svg>`;

  function renderLogo(variant, size, el){
    variant = variant || 'horizontal';
    size = size || 'md';
    el = el || document.getElementById('logo');
    if(!el) return;
    const sz = variant === 'vertical' ? 56 : (size==='lg'?52:size==='sm'?34:44);
    const cat = CAT.replace('class="lu-cat"', `class="lu-cat" width="${sz}" height="${sz}"`);
    el.className = 'lu-logo ' + variant + ' ' + size;
    el.innerHTML = cat + '<span class="lu-wordmark"><span class="lu-lu">Lulu</span> Downloader<span class="lu-sub">Media</span></span>';
  }
  window.renderLogo = renderLogo;
})();
