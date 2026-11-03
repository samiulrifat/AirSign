// Shows bill alerts as system notifications. Android Chrome only allows these
// through a service worker. There is no offline cache and no web push here.
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((open) => {
      if (open.length) return open[0].focus()
      return self.clients.openWindow('/')
    }),
  )
})
