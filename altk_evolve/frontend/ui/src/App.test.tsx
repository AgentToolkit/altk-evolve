import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import App from './App';

// Mock the global fetch so each route's useApi call resolves deterministically.
globalThis.fetch = vi.fn();

const dashboardData = {
    health: true,
    namespace_count: 1,
    total_entities: 3,
    approximate_type_breakdown: [{ type: 'guideline', count: 3 }],
    type_breakdown_is_approx: false,
    recent_entities: [],
};

const namespaces = [{ id: 'alpha-ns', amount_of_entities: 3 }];

const entities = [
    { id: 'e1', type: 'guideline', content: 'Hello entity', metadata: {} },
];

describe('App routing', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        // BrowserRouter reads the global history, which persists across tests — reset to root.
        window.history.pushState({}, '', '/');

        (globalThis.fetch as any).mockImplementation((url: string) => {
            const respond = (data: unknown) =>
                Promise.resolve({ ok: true, json: async () => data });
            if (url === '/api/dashboard') return respond(dashboardData);
            if (url === '/api/namespaces') return respond(namespaces);
            if (url.startsWith('/api/namespaces/alpha-ns/entities')) return respond(entities);
            return respond([]);
        });
    });

    it('renders the Dashboard on the index route', async () => {
        render(<App />);

        expect(await screen.findByText('Total Namespaces')).toBeInTheDocument();
    });

    it('navigates to Namespaces when the nav link is clicked and marks it active', async () => {
        const user = userEvent.setup();
        render(<App />);

        // Start on the Dashboard.
        await screen.findByText('Total Namespaces');

        await user.click(screen.getByRole('link', { name: /namespaces/i }));

        // The Namespaces view (page heading) is now rendered — the route changed.
        expect(await screen.findByRole('heading', { name: 'Namespaces' })).toBeInTheDocument();
        // useLocation drives the active class on the current nav link.
        expect(screen.getByRole('link', { name: /namespaces/i })).toHaveClass('active');
    });

    it('navigates from the namespace list to the entity explorer via a Link', async () => {
        const user = userEvent.setup();
        render(<App />);

        await user.click(screen.getByRole('link', { name: /namespaces/i }));

        // Wait for the namespace row (and its "View Entities" link) to appear.
        await user.click(await screen.findByTitle('View Entities'));

        // EntityExplorer rendered for the :id route...
        expect(
            await screen.findByText('Browse and filter entities within this namespace')
        ).toBeInTheDocument();
        // ...and useParams extracted the right id, driving the namespace-scoped fetch.
        expect(await screen.findByText('Hello entity')).toBeInTheDocument();
    });
});
