<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { settings, theme } from '$lib/stores';

	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	export let saveSettings: Function;

	const themes = ['dark', 'light', 'oled-dark'];
	const DEFAULT_ACCENT_HUE = 18;

	let selectedTheme = 'system';
	let accentHue = DEFAULT_ACCENT_HUE;
	let widescreenMode = false;
	let notificationEnabled = false;

	const applyAccentHue = () => {
		document.documentElement.style.setProperty('--gpthub-accent-hue', String(accentHue));
	};

	const toggleNotification = async () => {
		const permission = await Notification.requestPermission();

		if (permission === 'granted') {
			notificationEnabled = !notificationEnabled;
			saveSettings({ notificationEnabled });
		} else {
			toast.error(
				$i18n.t(
					'Response notifications cannot be activated as the website permissions have been denied. Please visit your browser settings to grant the necessary access.'
				)
			);
		}
	};

	onMount(() => {
		selectedTheme = localStorage.theme ?? 'system';
		accentHue = $settings?.accentHue ?? DEFAULT_ACCENT_HUE;
		widescreenMode = $settings?.widescreenMode ?? false;
		notificationEnabled = $settings?.notificationEnabled ?? false;
	});

	const applyTheme = (_theme: string) => {
		let themeToApply = _theme === 'oled-dark' ? 'dark' : _theme;

		if (_theme === 'system') {
			themeToApply = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
		}

		if (themeToApply === 'dark' && !_theme.includes('oled')) {
			document.documentElement.style.setProperty('--color-gray-800', '#333');
			document.documentElement.style.setProperty('--color-gray-850', '#262626');
			document.documentElement.style.setProperty('--color-gray-900', '#171717');
			document.documentElement.style.setProperty('--color-gray-950', '#0d0d0d');
		}

		themes
			.filter((e) => e !== themeToApply)
			.forEach((e) => {
				e.split(' ').forEach((e) => {
					document.documentElement.classList.remove(e);
				});
			});

		themeToApply.split(' ').forEach((e) => {
			document.documentElement.classList.add(e);
		});

		const metaThemeColor = document.querySelector('meta[name="theme-color"]');
		if (metaThemeColor) {
			if (_theme.includes('system')) {
				const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
					? 'dark'
					: 'light';
				metaThemeColor.setAttribute('content', systemTheme === 'light' ? '#ffffff' : '#171717');
			} else {
				metaThemeColor.setAttribute(
					'content',
					_theme === 'dark' ? '#171717' : _theme === 'oled-dark' ? '#000000' : '#ffffff'
				);
			}
		}

		if (typeof window !== 'undefined' && window.applyTheme) {
			window.applyTheme();
		}

		if (_theme.includes('oled')) {
			document.documentElement.style.setProperty('--color-gray-800', '#101010');
			document.documentElement.style.setProperty('--color-gray-850', '#050505');
			document.documentElement.style.setProperty('--color-gray-900', '#000000');
			document.documentElement.style.setProperty('--color-gray-950', '#000000');
			document.documentElement.classList.add('dark');
		}
	};

	const themeChangeHandler = (_theme: string) => {
		theme.set(_theme);
		localStorage.setItem('theme', _theme);
		applyTheme(_theme);
	};
</script>

<div class="flex flex-col h-full text-sm" id="tab-general">
	<div class="overflow-y-auto scrollbar-hidden h-full space-y-1">
		<div class="flex w-full justify-between">
			<div class="self-center text-xs font-medium">{$i18n.t('Theme')}</div>
			<div class="flex items-center relative">
				<select
					class="w-fit pr-8 rounded-sm py-2 px-2 text-xs bg-transparent text-right outline-hidden"
					bind:value={selectedTheme}
					placeholder={$i18n.t('Select a theme')}
					on:change={() => themeChangeHandler(selectedTheme)}
				>
					<option value="system">⚙️ {$i18n.t('System')}</option>
					<option value="dark">🌑 {$i18n.t('Dark')}</option>
					<option value="oled-dark">🌃 {$i18n.t('OLED Dark')}</option>
					<option value="light">☀️ {$i18n.t('Light')}</option>
				</select>
			</div>
		</div>

		<div class="py-0.5 flex w-full items-center justify-between gap-4">
			<label class="shrink-0 text-xs font-medium" for="accent-hue-slider">
				{$i18n.t('Accent Color')}
			</label>
			<input
				id="accent-hue-slider"
				class="gpthub-hue-slider flex-1 max-w-40"
				type="range"
				min="0"
				max="360"
				step="1"
				bind:value={accentHue}
				on:input={applyAccentHue}
				on:change={() => saveSettings({ accentHue })}
			/>
		</div>

		<div class="py-0.5 flex w-full justify-between">
			<div id="widescreen-mode-label" class="self-center text-xs font-medium">
				{$i18n.t('Widescreen Mode')}
			</div>
			<div class="flex items-center gap-2 p-1">
				<Switch
					ariaLabelledbyId="widescreen-mode-label"
					bind:state={widescreenMode}
					on:change={() => saveSettings({ widescreenMode })}
				/>
			</div>
		</div>

		<div class="py-0.5 flex w-full justify-between">
			<div class="self-center text-xs font-medium">{$i18n.t('Notifications')}</div>
			<button
				class="p-1 px-3 text-xs flex rounded-sm transition"
				on:click={toggleNotification}
				type="button"
				role="switch"
				aria-checked={notificationEnabled}
			>
				<span class="ml-2 self-center">{notificationEnabled ? $i18n.t('On') : $i18n.t('Off')}</span>
			</button>
		</div>
	</div>
</div>
