/* stitch-client.js — Integração runtime com Google Stitch API
 *
 * Carrega design tokens e assets via Stitch MCP (stitch.googleapis.com/mcp).
 * Se a API falhar (CORS, rede, auth), cai em fallback local com tokens
 * idênticos aos gerados pelo design system "Obsidian Stream".
 *
 * Uso:
 *   await StitchTheme.init({ projectId: '15410152418051530362', assetId: 'assets/64a991726f1d438b99cba2e41b2c6905' });
 *   // CSS variables --stitch-* injetadas em :root; evento 'stitch:ready' ou 'stitch:fallback' disparado
 *
 * API pública:
 *   StitchTheme.init(opts)   -> Promise<{source:'api'|'cache'|'fallback'}>
 *   StitchTheme.tokens       -> objeto com todos os tokens ativos
 *   StitchTheme.source       -> 'api' | 'cache' | 'fallback'
 *   StitchTheme.lastError    -> erro mais recente (ou null)
 *   StitchTheme.refresh()    -> força reload do design system
 *   StitchTheme.onReady(fn)  -> listener para 'stitch:ready' (API/cache)
 *   StitchTheme.onFallback(fn) -> listener para 'stitch:fallback' (fallback ativado)
 */
(function (global) {
  'use strict';

  const STITCH_ENDPOINT = 'https://stitch.googleapis.com/mcp';
  // API key do Google Stitch — em produção, vir de variável de ambiente do servidor
  // e ser injetada via <meta> ou endpoint proxy. Aqui uso o valor fornecido pelo operador.
  const STITCH_API_KEY = (typeof window !== 'undefined' && window.STITCH_API_KEY) || '';

  // Cache local (sessionStorage) — sobrevive navegação SPA, expira em 1h
  const CACHE_KEY = 'stitch:design_system:v1';
  const CACHE_TTL_MS = 60 * 60 * 1000;

  // Fallback local: tokens idênticos aos gerados pelo design system "Obsidian Stream"
  // (assets/64a991726f1d438b99cba2e41b2c6905, projeto 15410152418051530362).
  // Manter sincronizado com DESIGN.md se o design system for atualizado.
  const FALLBACK_TOKENS = {
    theme: {
      colorMode: 'DARK',
      roundness: 'ROUND_EIGHT',
      customColor: '#3b82f6',
      headlineFont: 'INTER',
      bodyFont: 'INTER',
      colorVariant: 'TONAL_SPOT'
    },
    colors: {
      'background': '#0b1326',
      'error': '#ffb4ab',
      'error_container': '#93000a',
      'inverse_on_surface': '#283044',
      'inverse_primary': '#0053db',
      'inverse_surface': '#dae2fd',
      'on_background': '#dae2fd',
      'on_error': '#690005',
      'on_error_container': '#ffdad6',
      'on_primary': '#002a78',
      'on_primary_container': '#eeefff',
      'on_primary_fixed': '#00174b',
      'on_primary_fixed_variant': '#003ea8',
      'on_secondary': '#003824',
      'on_secondary_container': '#00311f',
      'on_secondary_fixed': '#002113',
      'on_secondary_fixed_variant': '#005236',
      'on_surface': '#dae2fd',
      'on_surface_variant': '#c3c6d7',
      'on_tertiary': '#68000a',
      'on_tertiary_container': '#ffecea',
      'on_tertiary_fixed': '#410004',
      'on_tertiary_fixed_variant': '#930013',
      'outline': '#8d90a0',
      'outline_variant': '#434655',
      'primary': '#b4c5ff',
      'primary_container': '#2563eb',
      'primary_fixed': '#dbe1ff',
      'primary_fixed_dim': '#b4c5ff',
      'secondary': '#4edea3',
      'secondary_container': '#00a572',
      'secondary_fixed': '#6ffbbe',
      'secondary_fixed_dim': '#4edea3',
      'surface': '#0b1326',
      'surface_bright': '#31394d',
      'surface_container': '#171f33',
      'surface_container_high': '#222a3d',
      'surface_container_highest': '#2d3449',
      'surface_container_low': '#131b2e',
      'surface_container_lowest': '#060e20',
      'surface_dim': '#0b1326',
      'surface_tint': '#b4c5ff',
      'surface_variant': '#2d3449',
      'tertiary': '#ffb3ad',
      'tertiary_container': '#cf2c30',
      'tertiary_fixed': '#ffdad7',
      'tertiary_fixed_dim': '#ffb3ad'
    },
    styleGuidelines: 'Modern Technical dark UI for high-performance media downloader. Compact density, 4px base unit, tonal layering instead of shadows. Primary Blue + Emerald accents.'
  };

  // Map token → CSS var (kebab-case)
  const TOKEN_TO_CSS = {
    'primary': '--stitch-primary',
    'primary_container': '--stitch-primary-container',
    'primary_fixed': '--stitch-primary-fixed',
    'primary_fixed_dim': '--stitch-primary-fixed-dim',
    'on_primary': '--stitch-on-primary',
    'on_primary_container': '--stitch-on-primary-container',
    'on_primary_fixed': '--stitch-on-primary-fixed',
    'on_primary_fixed_variant': '--stitch-on-primary-fixed-variant',
    'secondary': '--stitch-secondary',
    'secondary_container': '--stitch-secondary-container',
    'secondary_fixed': '--stitch-secondary-fixed',
    'secondary_fixed_dim': '--stitch-secondary-fixed-dim',
    'on_secondary': '--stitch-on-secondary',
    'on_secondary_container': '--stitch-on-secondary-container',
    'on_secondary_fixed': '--stitch-on-secondary-fixed',
    'on_secondary_fixed_variant': '--stitch-on-secondary-fixed-variant',
    'tertiary': '--stitch-tertiary',
    'tertiary_container': '--stitch-tertiary-container',
    'tertiary_fixed': '--stitch-tertiary-fixed',
    'tertiary_fixed_dim': '--stitch-tertiary-fixed-dim',
    'on_tertiary': '--stitch-on-tertiary',
    'on_tertiary_container': '--stitch-on-tertiary-container',
    'on_tertiary_fixed': '--stitch-on-tertiary-fixed',
    'on_tertiary_fixed_variant': '--stitch-on-tertiary-fixed-variant',
    'background': '--stitch-background',
    'surface': '--stitch-surface',
    'surface_dim': '--stitch-surface-dim',
    'surface_bright': '--stitch-surface-bright',
    'surface_container_lowest': '--stitch-surface-container-lowest',
    'surface_container_low': '--stitch-surface-container-low',
    'surface_container': '--stitch-surface-container',
    'surface_container_high': '--stitch-surface-container-high',
    'surface_container_highest': '--stitch-surface-container-highest',
    'surface_variant': '--stitch-surface-variant',
    'surface_tint': '--stitch-surface-tint',
    'on_background': '--stitch-on-background',
    'on_surface': '--stitch-on-surface',
    'on_surface_variant': '--stitch-on-surface-variant',
    'outline': '--stitch-outline',
    'outline_variant': '--stitch-outline-variant',
    'inverse_surface': '--stitch-inverse-surface',
    'inverse_on_surface': '--stitch-inverse-on-surface',
    'inverse_primary': '--stitch-inverse-primary',
    'error': '--stitch-error',
    'error_container': '--stitch-error-container',
    'on_error': '--stitch-on-error',
    'on_error_container': '--stitch-on-error-container',
    'customColor': '--stitch-custom-color',
    'roundness': '--stitch-roundness',
    'headlineFont': '--stitch-headline-font',
    'bodyFont': '--stitch-body-font',
    'colorMode': '--stitch-color-mode'
  };

  // Estado do módulo
  const state = {
    tokens: null,    // tokens atualmente em uso
    source: null,    // 'api' | 'cache' | 'fallback'
    lastError: null,
    listeners: { ready: [], fallback: [] },
    inited: false
  };

  /* ---------- Cache helpers ---------- */

  function readCache() {
    try {
      const raw = sessionStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (Date.now() - data.cachedAt > CACHE_TTL_MS) return null;
      return data;
    } catch (e) {
      return null;
    }
  }

  function writeCache(tokens) {
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({
        tokens,
        cachedAt: Date.now()
      }));
    } catch (e) {
      // sessionStorage indisponível (modo privado) — silencioso
    }
  }

  /* ---------- API call via JSON-RPC ---------- */

  async function callStitch(method, params) {
    const body = JSON.stringify({
      jsonrpc: '2.0',
      method,
      params,
      id: Date.now()
    });

    // Timeout duro de 6s — se a API estiver lenta, fallback já
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 6000);

    try {
      const res = await fetch(STITCH_ENDPOINT, {
        method: 'POST',
        headers: {
          'X-Goog-Api-Key': STITCH_API_KEY,
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/event-stream'
        },
        body,
        signal: controller.signal
      });
      clearTimeout(timeoutId);

      if (!res.ok) {
        throw new Error(`HTTP ${res.status} ${res.statusText}`);
      }

      const text = await res.text();
      // Stitch devolve SSE-wrapped JSON: "data: {...}\n\n"
      // ou JSON puro. Tenta os dois.
      const jsonLine = text.split('\n')
        .map(l => l.trim())
        .find(l => l.startsWith('data:'));
      const jsonStr = jsonLine ? jsonLine.slice(5).trim() : text;
      const json = JSON.parse(jsonStr);

      if (json.error) {
        throw new Error(`Stitch error ${json.error.code}: ${json.error.message}`);
      }

      const result = json.result || {};
      if (result.isError) {
        const errText = (result.content && result.content[0] && result.content[0].text) || 'unknown';
        throw new Error(`Stitch tool error: ${errText}`);
      }

      // Preferir structuredContent (já parseado), cair no content[0].text
      if (result.structuredContent && typeof result.structuredContent === 'object') {
        return result.structuredContent;
      }
      if (result.content && Array.isArray(result.content) && result.content[0] && result.content[0].text) {
        try {
          return JSON.parse(result.content[0].text);
        } catch (e) {
          return result.content[0].text;
        }
      }
      return result;
    } catch (e) {
      clearTimeout(timeoutId);
      throw e;
    }
  }

  /* ---------- Token application ---------- */

  function applyTokens(tokens) {
    // Extrai theme e namedColors do response do Stitch
    const theme = tokens.theme || {};
    const colors = tokens.namedColors || tokens.colors || {};

    // Aplica theme-level tokens
    if (theme.colorMode) document.documentElement.setAttribute('data-stitch-mode', theme.colorMode);
    if (theme.roundness) {
      // Mapear ROUND_* → border-radius px
      const r = (theme.roundness || '').replace('ROUND_', '');
      const px = ({ ZERO: 0, FOUR: 4, EIGHT: 8, TWELVE: 12, FULL: 999 })[r] || 8;
      document.documentElement.style.setProperty('--stitch-radius-base', px + 'px');
    }
    if (theme.headlineFont) {
      const hFont = String(theme.headlineFont).toLowerCase().replace(/_/g, ' ');
      document.documentElement.style.setProperty('--stitch-headline-font', `"${hFont}", system-ui, sans-serif`);
    }
    if (theme.bodyFont) {
      const bFont = String(theme.bodyFont).toLowerCase().replace(/_/g, ' ');
      document.documentElement.style.setProperty('--stitch-body-font', `"${bFont}", system-ui, sans-serif`);
    }
    if (theme.customColor) {
      document.documentElement.style.setProperty('--stitch-custom-color', theme.customColor);
    }

    // Aplica named colors
    for (const [key, value] of Object.entries(colors)) {
      if (!value || typeof value !== 'string') continue;
      const cssVar = TOKEN_TO_CSS[key];
      if (cssVar) {
        document.documentElement.style.setProperty(cssVar, value);
      }
    }
  }

  function dispatchReady(source) {
    state.source = source;
    state.listeners.ready.forEach(fn => {
      try { fn({ tokens: state.tokens, source }); } catch (e) { console.warn('[stitch] ready listener error:', e); }
    });
    document.dispatchEvent(new CustomEvent('stitch:ready', { detail: { tokens: state.tokens, source } }));
  }

  function dispatchFallback(reason) {
    state.source = 'fallback';
    state.lastError = reason;
    state.tokens = FALLBACK_TOKENS;
    applyTokens(FALLBACK_TOKENS);
    state.listeners.fallback.forEach(fn => {
      try { fn({ tokens: state.tokens, error: reason }); } catch (e) { console.warn('[stitch] fallback listener error:', e); }
    });
    document.dispatchEvent(new CustomEvent('stitch:fallback', { detail: { tokens: state.tokens, error: reason } }));
  }

  /* ---------- Init ---------- */

  async function init(opts) {
    if (state.inited && state.tokens) {
      return { source: state.source };
    }
    state.inited = true;

    const { projectId, assetId, preferCache = true } = opts || {};

    // 1. Tenta cache primeiro
    if (preferCache) {
      const cached = readCache();
      if (cached && cached.tokens) {
        state.tokens = cached.tokens;
        applyTokens(cached.tokens);
        dispatchReady('cache');
        return { source: 'cache' };
      }
    }

    // 2. Tenta API
    if (!projectId) {
      dispatchFallback(new Error('No projectId provided'));
      return { source: 'fallback' };
    }

    try {
      // Lista design systems do projeto
      const dsList = await callStitch('tools/call', {
        name: 'list_design_systems',
        arguments: { projectId: String(projectId) }
      });

      const designSystems = (dsList && dsList.designSystems) || [];
      let chosen = null;

      if (assetId) {
        const target = assetId.replace(/^assets\//, '');
        chosen = designSystems.find(ds => (ds.name || '').endsWith(target) || ds.name === assetId);
      }
      if (!chosen && designSystems.length > 0) {
        // Pega o design system com displayName (ex: "Obsidian Stream")
        chosen = designSystems.find(ds => ds.designSystem && ds.designSystem.displayName)
                 || designSystems[designSystems.length - 1];
      }

      if (!chosen) {
        throw new Error('No design systems found in project ' + projectId);
      }

      // structuredContent: chosen.designSystem.theme.namedColors
      const ds = chosen.designSystem || {};
      const theme = ds.theme || {};
      const namedColors = theme.namedColors || ds.namedColors || {};

      const tokens = {
        assetName: chosen.name,
        version: chosen.version,
        displayName: ds.displayName || 'Stitch Design System',
        theme: {
          colorMode: theme.colorMode,
          roundness: theme.roundness,
          customColor: theme.customColor,
          headlineFont: theme.headlineFont,
          bodyFont: theme.bodyFont,
          colorVariant: theme.colorVariant
        },
        namedColors,
        colors: namedColors,
        styleGuidelines: ds.styleGuidelines || '',
        designMd: theme.designMd || ''
      };

      state.tokens = tokens;
      applyTokens(tokens);
      writeCache(tokens);
      dispatchReady('api');
      return { source: 'api' };
    } catch (err) {
      console.warn('[stitch] API call failed, using fallback:', err.message);
      dispatchFallback(err);
      return { source: 'fallback', error: err };
    }
  }

  async function refresh() {
    state.inited = false;
    state.tokens = null;
    state.source = null;
    state.lastError = null;
    try { sessionStorage.removeItem(CACHE_KEY); } catch (e) {}
    return init({ ...(init._lastOpts || {}) });
  }

  // Captura opts para refresh()
  const _origInit = init;
  global.StitchTheme = {
    init: function (opts) {
      init._lastOpts = opts;
      return _origInit(opts);
    },
    refresh,
    onReady: (fn) => state.listeners.ready.push(fn),
    onFallback: (fn) => state.listeners.fallback.push(fn),
    get tokens() { return state.tokens; },
    get source() { return state.source; },
    get lastError() { return state.lastError; }
  };
})(typeof window !== 'undefined' ? window : globalThis);
