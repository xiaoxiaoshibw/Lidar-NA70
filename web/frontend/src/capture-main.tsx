import { createRoot } from 'react-dom/client';
import { PageNav } from './PageNav';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <div>
    <PageNav />
    <main style={{ padding: '16px' }}>
      <h1>雷达与 IMU 标定采集</h1>
      <p>采集服务在 Jetson 本机运行，电脑通过本地 SSH 端口转发访问。若下方无法连接，请检查采集服务和端口转发。</p>
      <p>当前为开发验证阶段，现场采集请等待 AI 检查通过后通知。</p>
      <iframe title="标定采集控制台" src="http://127.0.0.1:8791/" style={{ width: '100%', minHeight: '1050px', border: '1px solid #30363d' }} />
    </main>
  </div>,
);
