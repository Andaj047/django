import asyncio
import json
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from django_backend.utils import saleor_get_user_id_from_token
from products.utils import (
    add_product_channel_list,
    create_digital_content,
    create_product_in_saleor,
    create_product_variant,
    product_channel_listing_update,
    saleor_get_products,
    saleor_product_delete,
    saleor_product_publish_status_update,
    update_product,
    update_product_variant_channel,
    update_product_variant_channel_listing,
)
from utilities.messages import *
from utilities.paginator import Paginator
from utilities.request_param_validators import validate_and_get_int
from vendors.models import Vendor, Vendor_products
from vendors.utils import check_if_vendor_exists_or_create, get_vendor_id_from_user_id

PAGE_SIZE = settings.REST_FRAMEWORK['PAGE_SIZE']

logger = logging.getLogger(__name__)


# Create your views here.
@api_view(["POST"])
def webhook_create_product(request):
    """
    This method is written to create a product in Saleor
    Headers: JWT_token
    Params: mutation_query for product
    Returns: product_id
    :param request:
    :return:
    """

    response_data = {}
    try:

        token = request.headers['Authorization']
        query = request.data['query']
        variables = json.dumps(request.data['variables'])
        variables = json.loads(variables)
        selling_price = variables['input']['selling_price']
        del variables['input']['selling_price']

        user_id = asyncio.run(saleor_get_user_id_from_token(token))
        if(user_id is None):
            response_data['message'] = "User doens't Exists in Saleor"
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        product_details = asyncio.run(
            create_product_in_saleor(query, variables))

        if(product_details is None):
            response_data['message'] = "Error while creating a product in Saleor"
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response_data['product'] = product_details
        product_id = product_details['productCreate']['product']['id']

        check_if_vendor_exists_or_create(user_id)
        Vendor.objects.get()
        Vendor_products.objects.create(
            product_id=product_id,
            vendor_id=user_id
        )

        response = asyncio.run(
            add_product_channel_list(product_id, product_details))

        response = asyncio.run(create_product_variant(product_id))
        product_variant_id = response['productVariantCreate']['productVariant']['id']

        response = asyncio.run(
            update_product_variant_channel(
                product_variant_id,
                selling_price)
        )

        response = asyncio.run(
            create_digital_content(
                product_variant_id
            )
        )

        response_data['message'] = "Product created successfully."
        response_data['success'] = 'true'
        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        message = 'Exception in create_product_api view ' + str(e)
        logger.debug(message)
        response_data['success'] = 'false'
        response_data['message'] = message
        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

@api_view(["POST"])
def webhook_edit_product(request):
    """
    This method is written to edit a product in Saleor
    Headers: JWT_token
    Params: mutation_query to edit the product
    Returns: edited product_id
    """
    response_data = {}
    try:
        query = request.data['query']
        variables = json.dumps(request.data['variables'])

        response = asyncio.run(update_product(query, variables))
        response_data = response

        response = asyncio.run(
            product_channel_listing_update(
                response['productVariantUpdate']['productVariant']['product']['id'],
                response['productUpdate']['product']['channelListings'][0]
            )
        )

        response = asyncio.run(
            update_product_variant_channel_listing(
                response['productChannelListingUpdate']['product']
            )
        )

        response_data['success'] = 'true'
        response_data['message'] = "Product updated successfully."
        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        message = 'Exception in edit_product_api view ' + str(e)
        logger.debug(message)
        response_data['message'] = message
        response_data['success'] = 'false'
        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)


@api_view(["DELETE"])
def webhook_delete_product(request):
    """
    Deletes a product from Saleor and this instance
    params: request
    return: response_data
    """

    response_data = {}

    try:
        # Get user id
        token = request.headers['Authorization']
        user_id = asyncio.run(saleor_get_user_id_from_token(token))

        if(user_id is None):
            response_data['message'] = messages_user['user_not_found_or_logged_out']
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        # Get data from request
        product_id = request.data['product_id']

        # Get vendor id from userid
        vendor_id = get_vendor_id_from_user_id(user_id)

        # Validate product is created by same user (vendor)
        product = Vendor_products.objects.values_list(
            'product_id', flat=True).filter(product_id=product_id, vendor_id=vendor_id)

        if not product:
            response_data['message'] = messages_product['product_does_not_exists_or_no_access']
            response_data['success'] = 'false'

            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        # TODO - Validate there are no orders against this product

        # Call Saleor APIs to delete
        response = asyncio.run(saleor_product_delete(product_id))

        if response:
            product = Vendor_products.objects.get(product_id=product_id, vendor_id=vendor_id)
            product.delete()

        response_data['message'] = messages_product['product_deleted_success']
        response_data['success'] = 'true'

        return Response(response_data, status=status.HTTP_200_OK)
    except Exception as e:
        response_data['message'] = str(e)
        response_data['success'] = 'false'

        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def unpublish_product(request):
    """
    Unpublishes a product at Saleor
    params: request
    return: response_data
    """

    response_data = {}

    try:
        # Get user id
        token = request.headers['Authorization']
        user_id = asyncio.run(saleor_get_user_id_from_token(token))

        if(user_id is None):
            response_data['message'] = messages_user['user_not_found_or_logged_out']

            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        # Get data from request
        product_id = request.data['product_id']

        # Get vendor id from userid
        vendor_id = get_vendor_id_from_user_id(user_id)

        # Validate product is created by same user (vendor)
        product = Vendor_products.objects.values_list(
            'product_id', flat=True).filter(product_id=product_id, vendor_id=vendor_id)

        if not product:
            response_data['message'] = messages_product['product_does_not_exists_or_no_access']
            response_data['success'] = 'false'

            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        # Get channel id
        channel_id = settings.DGV_CHANNEL_ID

        # Call Saleor APIs to unpublish
        response = asyncio.run(saleor_product_publish_status_update(product_id, channel_id, status="false"))

        response_data['message'] = messages_product['product_channel_listing_status_changed']

        return Response(response_data, status=status.HTTP_200_OK)
    except Exception as e:
        response_data['message'] = str(e)
        response_data['success'] = 'false'

        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
def get_products_list(request):
    """
    Returns list of products created by vendor
    params: request
    return: Products list
    """

    response_data = {}

    try:
        # Get user id
        token = request.headers['Authorization']
        user_id = asyncio.run(saleor_get_user_id_from_token(token))

        if(user_id is None):
            response_data['message'] = messages_user['user_not_found_or_logged_out']

            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        # Get vendor id from userid
        vendor_id = get_vendor_id_from_user_id(user_id)

        # Get filters from request
        isPublished = request.query_params.get('isPublished')
        if isPublished is None:
            isPublished = "true"

        # Get queryset / filtered queryset
        queryset = Vendor_products.objects.values_list('product_id', flat=True).filter(vendor_id=vendor_id)

        # Start - get paginated result
        page_number = request.query_params.get('page_number')
        if not page_number:
            page_number = 1

        page_number = validate_and_get_int("page_number", page_number, range_start=1)
        product_ids, number_of_pages, page_number = Paginator.paginate(queryset, page_number, PAGE_SIZE)
        # END - get paginated result

        product_ids = list(product_ids)

        if (len(product_ids) == 0):
            response_data['message'] = messages_product['no_products_found']
            response_data['data'] = []

            return Response(response_data, status=status.HTTP_200_OK)

        # Get product details from Saleor
        # Important to set same no. of results (first) at Saleor too
        filters = {'first': PAGE_SIZE, 'isPublished': isPublished}
        products = asyncio.run(saleor_get_products(product_ids, filters))

        response_data['page_number'] = page_number
        response_data['number_of_pages'] = number_of_pages
        response_data['data'] = products

        return Response(response_data, status=status.HTTP_200_OK)
    except Exception as e:
        response_data['message'] = str(e)
        response_data['success'] = 'false'

        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
