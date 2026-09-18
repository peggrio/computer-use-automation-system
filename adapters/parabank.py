"""Reference ParaBank release and readiness rules; no banking API calls."""
from urllib.parse import parse_qs, urlsplit

from automation.errors import UIError

IMAGE = 'sha256:747453a0c00b52ed36c7c55aa842a6e9e4a761f70ad577ec07b1f6cbee77e0e5'
RULES = {'overview_complete', 'activity_complete', 'search_complete', 'results_complete', 'details_complete'}


async def ready(adapter, rule):
    """Combine completed browser-originated loads with visible UI state.

    Metadata only: never request data through an API or read response bodies.
    """
    page = adapter.page
    path = urlsplit(page.url).path
    query = parse_qs(urlsplit(page.url).query)
    async def visible(selector):
        return await page.locator(selector).is_visible()
    async def text(selector):
        locator = page.locator(selector)
        return (await locator.inner_text()).strip() if await locator.count() == 1 else ''
    if rule == 'overview_complete':
        return (path == '/parabank/overview.htm' and adapter.completed(r'/parabank/services_proxy/bank/customers/[0-9]+/accounts')
                and await visible('#showOverview') and await page.locator('#accountTable tbody tr').count() > 0
                and await page.locator('#accountTable tbody tr').last.locator('td').first.inner_text() == 'Total')
    if rule == 'activity_complete':
        account = adapter.inputs['account_id']
        return (path == '/parabank/activity.htm' and query.get('id') == [account]
                and adapter.completed('/parabank/services_proxy/bank/accounts/' + account)
                and adapter.completed('/parabank/services_proxy/bank/accounts/' + account + '/transactions/month/All/type/All')
                and await text('#accountId') == account
                and await visible('#accountActivity')
                and ((await visible('#transactionTable') and await page.locator('#transactionTable tbody tr').count() > 0)
                     or await visible('#noTransactions')))
    if rule == 'search_complete':
        return (path == '/parabank/findtrans.htm' and await visible('#formContainer')
                and await page.locator('#accountId option').count() > 0
                and adapter.document_complete())
    if rule == 'results_complete':
        return (path == '/parabank/findtrans.htm'
                and adapter.completed('/parabank/services_proxy/bank/transactions/' + adapter.inputs['transaction_id'],
                                      allow_not_found=True, after=adapter.search_after)
                and await visible('#resultContainer') and not await visible('#formContainer'))
    if rule == 'details_complete':
        return (path == '/parabank/transaction.htm' and query.get('id') == [adapter.inputs['transaction_id']]
                and adapter.document_complete()
                and await page.get_by_role('heading', name='Transaction Details', exact=True).is_visible()
                and await adapter.read('detail_id') == adapter.inputs['transaction_id'])
    raise UIError('adapter_unsupported')
