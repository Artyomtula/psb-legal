// See https://kit.svelte.dev/docs/types#app
// for information about these interfaces
declare global {
	interface Window {
		electronAPI?: {
			send: (payload: { type: string; data?: unknown }) => Promise<any>;
			load: (path: string) => void;
			onEvent?: (handler: (event: any) => void) => void;
		};
		applyTheme?: () => void;
	}

	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface Platform {}
	}
}

export {};
