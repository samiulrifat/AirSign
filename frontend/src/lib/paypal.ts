// PayPal JavaScript SDK v6 for the buyer approval step.
//
// Our server creates the order when the payment intent is created, so the
// createOrder promise handed to session.start() resolves to that existing id.
// The server never captures here: capture happens only after the air signature.

const SANDBOX_SDK = 'https://www.sandbox.paypal.com/web-sdk/v6/core'

type OneTimeSession = {
  start: (options: { presentationMode: string }, order: Promise<{ orderId: string }>) => Promise<void>
}

type SdkInstance = {
  findEligibleMethods: (opts: { currencyCode: string }) => Promise<{ isEligible: (m: string) => boolean }>
  createPayPalOneTimePaymentSession: (callbacks: {
    onApprove: (data: { orderId: string }) => Promise<void> | void
    onCancel?: () => void
    onError?: (err: unknown) => void
  }) => OneTimeSession
}

type PayPalNamespace = {
  createInstance: (opts: { clientId: string; components: string[]; pageType?: string }) => Promise<SdkInstance>
}

declare global {
  interface Window {
    paypal?: PayPalNamespace
  }
}

let loading: Promise<PayPalNamespace> | null = null

function loadScript(): Promise<PayPalNamespace> {
  if (window.paypal) return Promise.resolve(window.paypal)
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const s = document.createElement('script')
      s.src = SANDBOX_SDK
      s.async = true
      s.onload = () => (window.paypal ? resolve(window.paypal) : reject(new Error('PayPal SDK did not initialise')))
      s.onerror = () => {
        loading = null
        reject(new Error('Could not load the PayPal SDK'))
      }
      document.head.appendChild(s)
    })
  }
  return loading
}

const instances = new Map<string, Promise<SdkInstance>>()

export function paypalInstance(clientId: string): Promise<SdkInstance> {
  let p = instances.get(clientId)
  if (!p) {
    p = loadScript().then((pp) => pp.createInstance({ clientId, components: ['paypal-payments'], pageType: 'checkout' }))
    instances.set(clientId, p)
  }
  return p
}

export async function paypalEligible(clientId: string, currencyCode: string): Promise<boolean> {
  const sdk = await paypalInstance(clientId)
  const methods = await sdk.findEligibleMethods({ currencyCode })
  return methods.isEligible('paypal')
}

export type ApprovalSession = { open: (orderId: string) => Promise<void> }

/**
 * Prepare the approval session before the buyer taps, then call open() straight
 * from the click handler. Nothing may be awaited between the click and start():
 * browsers only allow the PayPal window during the click's transient activation.
 */
export async function prepareApproval(
  clientId: string,
  handlers: { onApprove: () => Promise<void> | void; onCancel: () => void; onError: (e: unknown) => void },
): Promise<ApprovalSession> {
  const sdk = await paypalInstance(clientId)
  const session = sdk.createPayPalOneTimePaymentSession({
    onApprove: () => handlers.onApprove(),
    onCancel: handlers.onCancel,
    onError: handlers.onError,
  })
  return { open: (orderId) => session.start({ presentationMode: 'auto' }, Promise.resolve({ orderId })) }
}
