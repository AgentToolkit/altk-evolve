import { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Search, AlertCircle, RefreshCw, Layers, Plus } from 'lucide-react';
import { useDebounce } from '../hooks/useDebounce';
import { useApi } from '../hooks/useApi';
import EntityTable from './EntityTable';
import EntityDetailModal from './EntityDetailModal';
import CreateEntityModal from './CreateEntityModal';
import ConfirmDialog from './ConfirmDialog';
import type { Entity } from './EntityTable';

export default function EntityExplorer() {
    const { id } = useParams<{ id: string }>();
    const [filterType, setFilterType] = useState<string>("");
    const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
    const [isCreateOpen, setIsCreateOpen] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const [errorToast, setErrorToast] = useState<string | null>(null);

    const debouncedFilter = useDebounce(filterType, 300);

    // Compute apiUrl as null when id is missing so useApi skips fetching
    const apiUrl = id
        ? `/api/namespaces/${encodeURIComponent(id)}/entities${debouncedFilter ? `?type=${encodeURIComponent(debouncedFilter)}` : ''}`
        : null;
    const { data: entities, loading, error, refetch } = useApi<Entity[]>(apiUrl);

    const handleDelete = useCallback(async (entityId: string) => {
        if (!id) return;
        try {
            const res = await fetch(`/api/namespaces/${encodeURIComponent(id)}/entities/${encodeURIComponent(entityId)}`, {
                method: 'DELETE'
            });
            if (!res.ok) {
                const d = await res.json();
                throw new Error(d.detail || 'Failed to delete entity');
            }
            refetch();
            if (selectedEntity?.id === entityId) {
                setSelectedEntity(null);
            }
        } catch (err: any) {
            setErrorToast(err.message);
        } finally {
            setDeleteTarget(null);
        }
    }, [id, refetch, selectedEntity]);

    // Auto-dismiss error toast
    useEffect(() => {
        if (errorToast) {
            const timer = setTimeout(() => setErrorToast(null), 5000);
            return () => clearTimeout(timer);
        }
    }, [errorToast]);

    // Early return AFTER all hooks have been called
    if (!id) {
        return (
            <div className="entity-explorer-container">
                <div className="page-header explorer-header">
                    <h2 className="section-title text-danger">Error: Missing Namespace ID</h2>
                    <p className="text-secondary">Please return to the Namespaces page and try again.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="entity-explorer-container">
            <div className="page-header explorer-header">
                <div>
                    <h2 className="section-title d-flex align-center gap-2 explorer-title">
                        <Link to="/namespaces" className="btn-icon">
                            <ArrowLeft size={20} />
                        </Link>
                        <Layers size={22} className="text-primary" />
                        Namespace: {id}
                    </h2>
                    <p className="text-secondary explorer-subtitle">
                        Browse and filter entities within this namespace
                    </p>
                </div>
            </div>

            <div className="glass-panel toolbar">
                <div className="form-group no-margin flex-1">
                    <div className="search-box">
                        <Search size={18} className="text-secondary" />
                        <input
                            type="text"
                            placeholder="Filter by type (e.g. guideline)"
                            value={filterType}
                            onChange={(e) => setFilterType(e.target.value)}
                            className="search-input"
                        />
                    </div>
                </div>
                <button className="btn btn-primary" onClick={() => setIsCreateOpen(true)}>
                    <Plus size={18} /> Create Entity
                </button>
                <button className="btn btn-secondary" onClick={refetch}>
                    <RefreshCw size={18} /> Refresh
                </button>
            </div>

            {error ? (
                <div className="error-state">
                    <AlertCircle size={40} />
                    <p>{error}</p>
                    <button className="btn btn-secondary" onClick={refetch}>
                        <RefreshCw size={16} /> Retry
                    </button>
                </div>
            ) : loading ? (
                <div className="loading-state">
                    <div className="loader" data-testid="loader"></div>
                </div>
            ) : (
                <EntityTable
                    entities={entities || []}
                    onView={setSelectedEntity}
                    onDelete={(entityId) => setDeleteTarget(entityId)}
                />
            )}

            {selectedEntity && (
                <EntityDetailModal
                    entity={selectedEntity}
                    onClose={() => setSelectedEntity(null)}
                />
            )}

            {isCreateOpen && id && (
                <CreateEntityModal
                    namespaceId={id}
                    onClose={() => setIsCreateOpen(false)}
                    onCreated={() => {
                        setIsCreateOpen(false);
                        refetch();
                    }}
                />
            )}

            {deleteTarget && (
                <ConfirmDialog
                    title="Delete Entity"
                    message="Are you sure you want to delete this entity? This action cannot be undone."
                    confirmLabel="Delete"
                    variant="danger"
                    onConfirm={() => handleDelete(deleteTarget)}
                    onCancel={() => setDeleteTarget(null)}
                />
            )}

            {errorToast && (
                <div className="error-inline" style={{ position: 'fixed', bottom: '1.5rem', right: '1.5rem', zIndex: 100, maxWidth: '400px' }}>
                    {errorToast}
                </div>
            )}
        </div>
    );
}
