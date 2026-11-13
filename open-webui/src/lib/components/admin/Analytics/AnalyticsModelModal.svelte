<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { getModelChats } from '$lib/apis/analytics';

	import Modal from '$lib/components/common/Modal.svelte';
	import ChatList from '$lib/components/common/ChatList.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	export let show = false;
	export let model: { id: string; name: string } | null = null;
	export let startDate: number | null = null;
	export let endDate: number | null = null;
	export let onClose: () => void = () => {};

	const i18n = getContext<Writable<i18nType>>('i18n');

	const PAGE_SIZE = 50;

	let chatList: Array<{
		id: string;
		title: string;
		updated_at: number;
		user_id?: string;
		user_name?: string;
	}> = [];
	let chatListLoading = false;
	let allChatsLoaded = false;

	const close = () => {
		show = false;
		chatList = [];
		allChatsLoaded = false;
		onClose();
	};

	const loadChats = async () => {
		if (!model?.id) return;
		chatListLoading = true;
		chatList = [];
		allChatsLoaded = false;
		try {
			const res = await getModelChats(
				localStorage.token,
				model.id,
				startDate,
				endDate,
				0,
				PAGE_SIZE
			);
			const chats = res?.chats ?? [];
			chatList = chats.map((c: any) => ({
				id: c.chat_id,
				title: c.first_message || 'No preview',
				updated_at: c.updated_at,
				user_id: c.user_id,
				user_name: c.user_name
			}));
			allChatsLoaded = chats.length < PAGE_SIZE;
		} catch (err) {
			console.error('Failed to load chats:', err);
			chatList = [];
			allChatsLoaded = true;
		}
		chatListLoading = false;
	};

	const loadMoreChats = async () => {
		if (!model?.id || chatListLoading || allChatsLoaded) return;
		chatListLoading = true;
		try {
			const skip = chatList.length;
			const res = await getModelChats(
				localStorage.token,
				model.id,
				startDate,
				endDate,
				skip,
				PAGE_SIZE
			);
			const chats = res?.chats ?? [];
			const newChats = chats.map((c: any) => ({
				id: c.chat_id,
				title: c.first_message || 'No preview',
				updated_at: c.updated_at,
				user_id: c.user_id,
				user_name: c.user_name
			}));
			const existingIds = new Set(chatList.map((c) => c.id));
			const uniqueNewChats = newChats.filter((c) => !existingIds.has(c.id));
			chatList = [...chatList, ...uniqueNewChats];
			allChatsLoaded = chats.length < PAGE_SIZE;
		} catch (err) {
			console.error('Failed to load more chats:', err);
		}
		chatListLoading = false;
	};

	$: if (show && model?.id) {
		loadChats();
	}
</script>

<Modal size="md" bind:show>
	{#if model}
		<div class="flex justify-between dark:text-gray-300 px-5 pt-4 pb-2">
			<Tooltip content={`${model.name} (${model.id})`} placement="top-start">
				<div class="text-lg font-medium self-center line-clamp-1">
					{model.name}
				</div>
			</Tooltip>
			<button class="self-center" on:click={close} aria-label="Close">
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="px-5 pb-4 dark:text-gray-200">
			<div class="mt-3">
				<ChatList
					{chatList}
					loading={chatListLoading}
					allLoaded={allChatsLoaded}
					showUserInfo={true}
					shareUrl={true}
					onLoadMore={loadMoreChats}
					onChatClick={() => (show = false)}
				/>
			</div>

			<div class="flex justify-end pt-4">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
					type="button"
					on:click={close}
				>
					{$i18n.t('Close')}
				</button>
			</div>
		</div>
	{/if}
</Modal>
