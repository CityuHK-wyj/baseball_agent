import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './style.css';
const client = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 1000 } } });
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><BrowserRouter><App /></BrowserRouter></QueryClientProvider>);
