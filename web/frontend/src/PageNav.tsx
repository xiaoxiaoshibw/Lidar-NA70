// Workbench navigation: production routes are served by FastAPI, Vite uses HTML entries.
const PAGES = [
  { key: 'main', label: '主工作台', sub: '回放 · 建图', href: '/', devHref: '/' },
  { key: 'annotate', label: '标注工作台', sub: '离线精标', href: '/annotate/', devHref: '/annotate.html' },
  { key: 'demo', label: '跟踪 Demo', sub: '球门识别 · 跟踪', href: '/demo/', devHref: '/demo.html' },
  { key: 'wire', label: '导线工作台', sub: 'W1 原始回波', href: '/wire/', devHref: '/wire.html' },
  { key: 'capture', label: '标定采集', sub: '雷达 · IMU', href: '/capture/', devHref: '/capture.html' },
  { key: 'local-capture', label: '本机双流接收', sub: 'IMU USB · 雷达网口', href: '/local-capture/', devHref: '/local-capture.html' },
];

function currentPage(): string {
  const path = window.location.pathname;
  if (path.startsWith('/annotate')) return 'annotate';
  if (path.startsWith('/demo')) return 'demo';
  if (path.startsWith('/wire')) return 'wire';
  if (path.startsWith('/local-capture')) return 'local-capture';
  if (path.startsWith('/capture')) return 'capture';
  return 'main';
}

export function PageNav() {
  const current = currentPage();
  return (
    <nav className="page-nav" aria-label="工作台切换">
      {PAGES.map((page) => (
        <a key={page.key}
           href={import.meta.env.DEV ? page.devHref : page.href}
           className={page.key === current ? 'active' : ''}>
          <strong>{page.label}</strong>
          <small>{page.sub}</small>
        </a>
      ))}
    </nav>
  );
}
