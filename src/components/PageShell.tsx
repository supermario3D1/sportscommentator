import type { PageId } from '../types';

interface PageShellProps {
  page: PageId;
  setPage: (page: PageId) => void;
  canOpenWorkflow: boolean;
}

const pages: Array<{ id: PageId; label: string }> = [
  { id: 'home', label: 'Home' },
  { id: 'upload', label: 'Upload' },
  { id: 'voice', label: 'Voice Analysis' },
  { id: 'processing', label: 'Processing' },
  { id: 'preview', label: 'Live Preview' },
  { id: 'editor', label: 'Script Editor' },
  { id: 'export', label: 'Export' },
];

export function PageNav({ page, setPage, canOpenWorkflow }: PageShellProps) {
  return (
    <nav className="page-nav" aria-label="Workflow pages">
      {pages.map((item) => {
        const disabled = item.id !== 'home' && item.id !== 'upload' && !canOpenWorkflow;
        return (
          <button
            key={item.id}
            type="button"
            className={page === item.id ? 'is-active' : undefined}
            onClick={() => setPage(item.id)}
            disabled={disabled}
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
