// System notifications for incoming bills, where the browser allows them.
// iPhones only allow them for apps added to the home screen, so the bill card
// and the sound on the home screen always work on their own.

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null
  try {
    await navigator.serviceWorker.register('/sw.js')
    return await navigator.serviceWorker.ready
  } catch {
    return null
  }
}

/** Call from a tap: browsers only show the permission prompt after one. */
export async function askNotifications(): Promise<boolean> {
  if (!('Notification' in window)) return false
  const permission = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
  if (permission !== 'granted') return false
  await registration()
  return true
}

export async function notify(title: string, body: string, tag: string): Promise<void> {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  const reg = await registration()
  try {
    if (reg) await reg.showNotification(title, { body, tag, icon: '/favicon.svg' })
    else new Notification(title, { body, tag, icon: '/favicon.svg' })
  } catch {
    /* the card on the home screen still shows the bill */
  }
}
