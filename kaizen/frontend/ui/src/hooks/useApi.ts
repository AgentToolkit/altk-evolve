import { useEffect, useState, useCallback } from 'react';

interface UseApiResult<T> {
    data: T | null;
    loading: boolean;
    error: string | null;
    refetch: () => void;
}

/**
 * Shared data-fetching hook that handles loading, error, and refetch logic.
 * Replaces the duplicated fetch → setLoading → setError → setData pattern.
 */
export function useApi<T>(url: string | null): UseApiResult<T> {
    const [data, setData] = useState<T | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const refetch = useCallback(() => {
        if (!url) return;
        setLoading(true);
        fetch(url)
            .then(res => {
                if (!res.ok) throw new Error(`Failed to fetch ${url}`);
                return res.json();
            })
            .then(d => {
                setData(d);
                setError(null);
            })
            .catch(err => setError(err instanceof Error ? err.message : String(err)))
            .finally(() => setLoading(false));
    }, [url]);

    useEffect(() => {
        refetch();
    }, [refetch]);

    return { data, loading, error, refetch };
}
