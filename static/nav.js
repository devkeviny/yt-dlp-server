// Lulu Downloader — menu UNICO compartilhado por todas as paginas.
// Renderiza: header fixo (desktop com links, mobile com hamburguer),
// drawer lateral mobile e botao Sair. Auto-contido (CSS proprio) para
// nao depender do tailwind.config de cada pagina.
// Uso: <div id="app-nav"></div> no inicio do <body> +
//      <script src="/static/nav.js"></script> antes de </body>.
(function () {
  var ITEMS = [
    { href: '/', icon: 'download', label: 'Conversor' },
    { href: '/dashboard', icon: 'dashboard', label: 'Dashboard' },
    { href: '/transfers', icon: 'history', label: 'Histórico' },
    { href: '/storage', icon: 'database', label: 'Armazenamento' },
    { href: '/proxies', icon: 'hub', label: 'Proxies' },
    { href: '/api', icon: 'api', label: 'API' },
    { href: '/system', icon: 'settings', label: 'Sistema' }
  ];

  var CSS = '\
.lx-header{position:fixed;top:0;left:0;right:0;height:64px;z-index:50;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:rgba(19,19,19,.72);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,255,255,.1);font-family:Inter,system-ui,sans-serif}\
.lx-left{display:flex;align-items:center;gap:10px}\
.lx-burger{display:none;background:none;border:0;color:#e5e2e1;padding:8px;margin-left:-8px;cursor:pointer}\
.lx-nav{display:flex;align-items:center;gap:28px}\
.lx-nav a{color:#b9cacb;text-decoration:none;font-size:14px;transition:color .15s}\
.lx-nav a:hover{color:#00f0ff}\
.lx-nav a.lx-active{color:#00f0ff;border-bottom:2px solid #00f0ff;padding-bottom:4px}\
.lx-logout{display:flex;align-items:center;gap:6px;background:none;border:0;color:#b9cacb;font-size:12px;cursor:pointer;transition:color .15s}\
.lx-logout:hover{color:#ff6b6b}\
.lx-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:55}\
.lx-drawer{position:fixed;top:0;left:0;height:100%;width:256px;z-index:60;display:flex;flex-direction:column;background:rgba(14,14,14,.97);border-right:1px solid rgba(255,255,255,.1);transform:translateX(-105%);transition:transform .22s ease;font-family:Inter,system-ui,sans-serif}\
.lx-drawer.lx-open{transform:translateX(0)}\
.lx-drawer-head{display:flex;align-items:center;justify-content:space-between;height:64px;padding:0 20px}\
.lx-drawer nav{flex:1;padding:8px 16px;display:flex;flex-direction:column;gap:6px}\
.lx-drawer nav a{display:flex;align-items:center;gap:14px;padding:12px 16px;border-radius:10px;color:#b9cacb;text-decoration:none;font-size:14px}\
.lx-drawer nav a:hover{background:rgba(255,255,255,.06)}\
.lx-drawer nav a.lx-active{background:rgba(0,240,255,.14);color:#00f0ff;border-right:4px solid #00f0ff}\
.lx-drawer-foot{padding:16px}\
.lx-drawer-foot button{width:100%;padding:12px;border:0;border-radius:12px;background:#00f0ff;color:#00363a;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px}\
.lx-hidden{display:none}\
@media (max-width:767px){.lx-nav{display:none}.lx-logout{display:none}.lx-burger{display:block}}\
';

  function isActive(href) {
    var p = window.location.pathname.replace(/\/+$/, '') || '/';
    return p === href;
  }

  function doLogout() {
    document.cookie = 'auth_session=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    window.location.href = '/logout';
  }

  function render() {
    var mount = document.getElementById('app-nav');
    if (!mount) return;
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    var desktop = ITEMS.map(function (it) {
      return '<a href="' + it.href + '"' + (isActive(it.href) ? ' class="lx-active"' : '') + '>' + it.label + '</a>';
    }).join('');
    var mobile = ITEMS.map(function (it) {
      return '<a href="' + it.href + '"' + (isActive(it.href) ? ' class="lx-active"' : '') + '><span class="material-symbols-outlined">' + it.icon + '</span><span>' + it.label + '</span></a>';
    }).join('');

    mount.innerHTML =
      '<header class="lx-header">' +
        '<div class="lx-left">' +
          '<button class="lx-burger" id="lx-burger" aria-label="Abrir menu"><span class="material-symbols-outlined">menu</span></button>' +
          '<span id="logo-nav"></span>' +
        '</div>' +
        '<nav class="lx-nav">' + desktop + '</nav>' +
        '<button class="lx-logout" id="lx-logout"><span class="material-symbols-outlined" style="font-size:16px">logout</span>Sair</button>' +
      '</header>' +
      '<div class="lx-backdrop lx-hidden" id="lx-backdrop"></div>' +
      '<aside class="lx-drawer" id="lx-drawer">' +
        '<div class="lx-drawer-head"><span id="logo-drawer"></span>' +
        '<button class="lx-burger" id="lx-close" aria-label="Fechar menu" style="display:block"><span class="material-symbols-outlined">close</span></button></div>' +
        '<nav>' + mobile + '</nav>' +
        '<div class="lx-drawer-foot"><button id="lx-logout-m"><span class="material-symbols-outlined">logout</span>Sair</button></div>' +
      '</aside>';

    if (window.renderLogo) {
      var l1 = document.getElementById('logo-nav');
      if (l1) window.renderLogo('horizontal', 'sm', l1);
      var l2 = document.getElementById('logo-drawer');
      if (l2) window.renderLogo('horizontal', 'sm', l2);
    }

    var drawer = document.getElementById('lx-drawer');
    var backdrop = document.getElementById('lx-backdrop');
    function open() { drawer.classList.add('lx-open'); backdrop.classList.remove('lx-hidden'); }
    function close() { drawer.classList.remove('lx-open'); backdrop.classList.add('lx-hidden'); }
    document.getElementById('lx-burger').addEventListener('click', open);
    document.getElementById('lx-close').addEventListener('click', close);
    backdrop.addEventListener('click', close);
    document.getElementById('lx-logout').addEventListener('click', doLogout);
    document.getElementById('lx-logout-m').addEventListener('click', doLogout);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();
})();
