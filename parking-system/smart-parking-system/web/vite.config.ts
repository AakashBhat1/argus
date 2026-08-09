import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) => filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			adapter: adapter()
		})
	],
	server: {
		proxy: {
			'/api': {
				target: 'http://localhost:5000',
				changeOrigin: true,
				ws: true
			},
			'/feed': {
				target: 'http://localhost:5000',
				changeOrigin: true
			}
		}
	}
});
